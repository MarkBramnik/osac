/*
Copyright (c) 2026 Red Hat Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See
the License for the specific language governing permissions and limitations
under the License.
*/

package it

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/rpc/jsonrpc"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	. "github.com/onsi/ginkgo/v2/dsl/core"
	. "github.com/onsi/gomega"
)

const fulfillmentDebugInstallTimeout = 10 * time.Minute

type fulfillmentDebugWorkload struct {
	app       string
	container string
	process   string
}

var _ = Describe("Fulfillment debug image", func() {
	It("accepts a Delve attach to all four debugged processes", func() {
		debugImage := os.Getenv("IT_DEBUG_IMAGE")
		if debugImage == "" {
			Skip("the focused debug-image integration phase is not enabled")
		}
		normalImage := os.Getenv("IT_DEBUG_NORMAL_IMAGE")
		Expect(normalImage).NotTo(BeEmpty())

		profile := fulfillmentDebugEnvOrDefault("IT_DEBUG_PROFILE", "dev")
		namespace := fulfillmentDebugEnvOrDefault("IT_DEBUG_NAMESPACE", "osac")
		clusterName := fulfillmentDebugEnvOrDefault("IT_DEBUG_CLUSTER_NAME", "osac-dev")
		extraHelmArgs := os.Getenv("IT_DEBUG_EXTRA_HELM_ARGS")
		Expect(extraHelmArgs).NotTo(BeEmpty(), "preserve the integration profile during the debug upgrade")

		DeferCleanup(func() {
			By("restoring the normal Fulfillment image and settings")
			Expect(installFulfillmentDebugImage(false, normalImage, profile, namespace,
				clusterName, extraHelmArgs)).To(Succeed())
		})

		By("upgrading only Fulfillment to the real image with Delve paused")
		Expect(installFulfillmentDebugImage(true, debugImage, profile, namespace,
			clusterName, extraHelmArgs)).To(Succeed())

		workloads := []fulfillmentDebugWorkload{
			{app: "fulfillment-controller", container: "controller", process: "controller"},
			{app: "fulfillment-grpc-server", container: "grpc-server", process: "grpc-server"},
			{app: "fulfillment-rest-gateway", container: "rest-gateway", process: "rest-gateway"},
			{app: "fulfillment-event-publisher", container: "event-publisher", process: "event-publisher"},
		}
		for _, workload := range workloads {
			By("attaching to " + workload.process)
			pod := waitForFulfillmentDebugPod(namespace, workload, debugImage)
			Expect(pod.Spec.ServiceAccountName).To(Equal("debug"))

			container := fulfillmentPodContainer(pod, workload.container)
			Expect(container.Image).To(Equal(debugImage))
			Expect(container.Command).To(Equal([]string{"/usr/local/bin/debug-entrypoint"}))
			Expect(len(container.Args)).To(BeNumerically(">=", 2))
			Expect(container.Args[:2]).To(Equal([]string{"start", workload.process}))
			Expect(envValue(container.Env, "OSAC_DEBUG_CONTINUE")).To(Equal("false"))
			Expect(container.SecurityContext.Capabilities.Add).To(ContainElement("SYS_PTRACE"))
			Expect(pod.Spec.SecurityContext.SeccompProfile.Type).To(Equal("Unconfined"))

			localPort, stopPortForward, err := startFulfillmentDebugPortForward(namespace, pod.Metadata.Name)
			Expect(err).NotTo(HaveOccurred())
			DeferCleanup(stopPortForward)

			threadCount, running, err := inspectFulfillmentDebuggee(localPort)
			Expect(err).NotTo(HaveOccurred())
			Expect(threadCount).To(BeNumerically(">", 0))
			Expect(running).To(BeFalse(), "the debuggee remains paused until the IDE resumes it")
			stopPortForward()
		}
	})
})

type fulfillmentDebugContainer struct {
	Name            string   `json:"name"`
	Image           string   `json:"image"`
	Command         []string `json:"command"`
	Args            []string `json:"args"`
	Env             []struct{ Name, Value string }
	SecurityContext struct {
		Capabilities struct {
			Add []string
		} `json:"capabilities"`
	} `json:"securityContext"`
}

type fulfillmentDebugPod struct {
	Metadata struct {
		Name string `json:"name"`
	} `json:"metadata"`
	Spec struct {
		ServiceAccountName string `json:"serviceAccountName"`
		SecurityContext    struct {
			SeccompProfile struct {
				Type string `json:"type"`
			} `json:"seccompProfile"`
		} `json:"securityContext"`
		Containers []fulfillmentDebugContainer `json:"containers"`
	} `json:"spec"`
	Status struct {
		Phase      string `json:"phase"`
		Conditions []struct {
			Type   string `json:"type"`
			Status string `json:"status"`
		} `json:"conditions"`
	} `json:"status"`
}

func installFulfillmentDebugImage(debug bool, image, profile, namespace, clusterName, extraHelmArgs string) error {
	installerDir, err := findFulfillmentInstallerDir()
	if err != nil {
		return err
	}
	args := []string{
		"-C", installerDir, "install-services",
		"PLATFORM=kind",
		"PROFILE=" + profile,
		"NS=" + namespace,
		"KIND_CLUSTER_NAME=" + clusterName,
		"BUILD=false",
		"PUSH=false",
		"DEBUG=" + strconv.FormatBool(debug),
		"SERVICES=fulfillment-service",
		"FULFILLMENT_SERVICE_IMAGE=" + image,
		"EXTRA_HELM_ARGS=" + extraHelmArgs,
	}
	if debug {
		args = append(args, "FULFILLMENT_DEBUG_CONTINUE=false")
	}
	ctx, cancel := context.WithTimeout(context.Background(), fulfillmentDebugInstallTimeout)
	defer cancel()
	output, err := exec.CommandContext(ctx, "make", args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("make %s failed: %w\n%s", strings.Join(args, " "), err, output)
	}
	return nil
}

func findFulfillmentInstallerDir() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return filepath.Join(dir, "..", "osac-installer"), nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("could not find fulfillment-service module root")
		}
		dir = parent
	}
}

func waitForFulfillmentDebugPod(namespace string, workload fulfillmentDebugWorkload, debugImage string) fulfillmentDebugPod {
	var readyPod fulfillmentDebugPod
	Eventually(func() error {
		output, err := runFulfillmentDebugCommand(15*time.Second, "kubectl", "get", "pods",
			"--namespace", namespace, "-l", "app="+workload.app, "-o", "json")
		if err != nil {
			return err
		}
		var podList struct {
			Items []fulfillmentDebugPod `json:"items"`
		}
		if err := json.Unmarshal(output, &podList); err != nil {
			return err
		}
		for _, pod := range podList.Items {
			if pod.Status.Phase != "Running" || !podReady(pod) {
				continue
			}
			container := fulfillmentPodContainer(pod, workload.container)
			if container.Name != workload.container || container.Image != debugImage {
				continue
			}
			readyPod = pod
			return nil
		}
		return fmt.Errorf("no ready debug Pod found for app=%s", workload.app)
	}, 3*time.Minute, time.Second).Should(Succeed())
	return readyPod
}

func podReady(pod fulfillmentDebugPod) bool {
	for _, condition := range pod.Status.Conditions {
		if condition.Type == "Ready" {
			return condition.Status == "True"
		}
	}
	return false
}

func fulfillmentPodContainer(pod fulfillmentDebugPod, name string) fulfillmentDebugContainer {
	for _, container := range pod.Spec.Containers {
		if container.Name == name {
			return container
		}
	}
	return fulfillmentDebugContainer{}
}

func envValue(env []struct{ Name, Value string }, name string) string {
	for _, item := range env {
		if item.Name == name {
			return item.Value
		}
	}
	return ""
}

func fulfillmentDebugEnvOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func runFulfillmentDebugCommand(timeout time.Duration, name string, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	return exec.CommandContext(ctx, name, args...).CombinedOutput()
}

func startFulfillmentDebugPortForward(namespace, podName string) (int, func(), error) {
	ctx, cancel := context.WithCancel(context.Background())
	cmd := exec.CommandContext(ctx, "kubectl", "port-forward", "--address=127.0.0.1",
		"--namespace", namespace, "pod/"+podName, ":2345")
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		cancel()
		return 0, nil, err
	}
	cmd.Stderr = GinkgoWriter
	if err := cmd.Start(); err != nil {
		cancel()
		return 0, nil, err
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	lines := make(chan string, 8)
	go func() {
		scanner := bufio.NewScanner(stdout)
		for scanner.Scan() {
			lines <- scanner.Text()
		}
		if err := scanner.Err(); err != nil {
			lines <- err.Error()
		}
		close(lines)
	}()

	var stopOnce sync.Once
	stop := func() {
		stopOnce.Do(func() {
			cancel()
			select {
			case <-done:
			case <-time.After(5 * time.Second):
				_ = cmd.Process.Kill()
				<-done
			}
		})
	}
	timer := time.NewTimer(30 * time.Second)
	defer timer.Stop()
	for {
		select {
		case line, ok := <-lines:
			if !ok {
				stop()
				return 0, nil, fmt.Errorf("kubectl port-forward exited before opening a local port")
			}
			const prefix = "Forwarding from 127.0.0.1:"
			if !strings.HasPrefix(line, prefix) {
				continue
			}
			portText, _, _ := strings.Cut(strings.TrimPrefix(line, prefix), " -> ")
			port, err := strconv.Atoi(portText)
			if err != nil {
				stop()
				return 0, nil, fmt.Errorf("parse port-forward output %q: %w", line, err)
			}
			return port, stop, nil
		case err := <-done:
			cancel()
			if err == nil {
				return 0, nil, fmt.Errorf("kubectl port-forward exited before opening a local port")
			}
			return 0, nil, fmt.Errorf("kubectl port-forward exited before opening a local port: %w", err)
		case <-timer.C:
			stop()
			return 0, nil, fmt.Errorf("timed out waiting for kubectl port-forward")
		}
	}
}

func inspectFulfillmentDebuggee(localPort int) (int, bool, error) {
	address := net.JoinHostPort("127.0.0.1", strconv.Itoa(localPort))
	conn, err := net.DialTimeout("tcp", address, 5*time.Second)
	if err != nil {
		return 0, false, err
	}
	defer conn.Close()
	if err := conn.SetDeadline(time.Now().Add(10 * time.Second)); err != nil {
		return 0, false, err
	}
	client := jsonrpc.NewClient(conn)
	defer client.Close()
	var stateReply struct {
		State json.RawMessage
	}
	if err := client.Call("RPCServer.State", struct {
		NonBlocking bool
	}{NonBlocking: true}, &stateReply); err != nil {
		return 0, false, err
	}
	var state struct {
		Running bool
	}
	if err := json.Unmarshal(stateReply.State, &state); err != nil {
		return 0, false, err
	}
	var reply struct {
		Threads []json.RawMessage
	}
	if err := client.Call("RPCServer.ListThreads", struct{}{}, &reply); err != nil {
		return 0, false, err
	}
	return len(reply.Threads), state.Running, nil
}
