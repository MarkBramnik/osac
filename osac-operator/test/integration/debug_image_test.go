/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package integration

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/rpc/jsonrpc"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive,staticcheck
	. "github.com/onsi/gomega"    //nolint:revive,staticcheck

	"github.com/osac-project/osac/osac-operator/test/utils"
)

const debugImageAttachTimeout = 3 * time.Minute

var _ = Describe("Operator debug image", func() {
	It("starts the real image and accepts a Delve attach", func() {
		debugImage := os.Getenv("OSAC_OPERATOR_DEBUG_IMAGE")
		Expect(debugImage).NotTo(BeEmpty(), "make integration-tests builds and loads the debug image")

		suffix := strconv.FormatInt(time.Now().UnixNano(), 36)
		namespace := "osac-debug-attach-" + suffix
		release := "osac-debug-attach-" + suffix

		By("creating an isolated namespace and its required CA bundle")
		_, err := runDebugImageCommand(30*time.Second, "kubectl", "create", "namespace", namespace)
		Expect(err).NotTo(HaveOccurred())
		DeferCleanup(cleanupDebugImageRelease, release, namespace)

		_, err = runDebugImageCommand(30*time.Second, "kubectl", "create", "configmap", "ca-bundle",
			"--namespace", namespace, "--from-literal=bundle.pem=integration-test")
		Expect(err).NotTo(HaveOccurred())

		By("installing the operator chart with the locally built debug image paused in Delve")
		_, err = runDebugImageCommand(4*time.Minute, "helm", "upgrade", "--install", release, "charts/operator",
			"--namespace", namespace,
			"--set", "debug.enabled=true",
			"--set-string", "debug.image="+debugImage,
			"--set", "debug.imagePullPolicy=Never",
			"--set", "debug.continue=false",
			"--set", "debug.fsGroup=1001",
			"--set", "controllers.tenant=false",
			"--set", "controllers.storage=false",
			"--set", "controllers.volume=false",
			"--set", "controllers.networking=true",
			"--set", "controllers.networkingProvisioning=false",
			"--set", "global.services.caas.enabled=false",
			"--set", "global.services.vmaas.enabled=false",
			"--set", "global.services.bmaas.enabled=false",
			"--set", "networkManagers.enabled=false",
			"--set", "consoleProxy.disabled=true",
			"--wait", "--timeout", "3m")
		Expect(err).NotTo(HaveOccurred())

		selector := "app.kubernetes.io/instance=" + release + ",app.kubernetes.io/name=osac-operator"
		By("checking the deployed Pod uses the Helm debug identity and image")
		var podName string
		Eventually(func() error {
			output, err := runDebugImageCommand(15*time.Second, "kubectl", "get", "pods",
				"--namespace", namespace, "-l", selector,
				"-o", "jsonpath={.items[0].metadata.name}")
			if err != nil {
				return err
			}
			podName = strings.TrimSpace(string(output))
			if podName == "" {
				return fmt.Errorf("no debug Pod found for release %s", release)
			}
			return nil
		}, debugImageAttachTimeout, time.Second).Should(Succeed())

		output, err := runDebugImageCommand(15*time.Second, "kubectl", "get", "pod", podName,
			"--namespace", namespace, "-o", "jsonpath={.spec.serviceAccountName}")
		Expect(err).NotTo(HaveOccurred())
		Expect(strings.TrimSpace(string(output))).To(Equal("debug"))

		output, err = runDebugImageCommand(15*time.Second, "kubectl", "get", "pod", podName,
			"--namespace", namespace, "-o", "jsonpath={.spec.containers[0].image}")
		Expect(err).NotTo(HaveOccurred())
		Expect(strings.TrimSpace(string(output))).To(Equal(debugImage))

		By("waiting for Delve's pod-local endpoint to be available through port-forward")
		localPort, stopPortForward, err := startDebugPortForward(namespace, podName)
		Expect(err).NotTo(HaveOccurred())
		DeferCleanup(stopPortForward)

		By("attaching to the paused manager and listing its real debuggee thread")
		var threadCount int
		var running bool
		Eventually(func() error {
			threadCount, running, err = inspectRemoteDebuggee(localPort)
			if err != nil {
				return err
			}
			if threadCount == 0 {
				return fmt.Errorf("Delve attached but returned no manager threads")
			}
			if running {
				return fmt.Errorf("manager continued before a debugger requested resume")
			}
			return nil
		}, 30*time.Second, time.Second).Should(Succeed())
	})
})

func runDebugImageCommand(timeout time.Duration, name string, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	return utils.Run(exec.CommandContext(ctx, name, args...))
}

func cleanupDebugImageRelease(release, namespace string) {
	_, uninstallErr := runDebugImageCommand(60*time.Second, "helm", "uninstall", release,
		"--namespace", namespace, "--ignore-not-found", "--wait", "--timeout", "45s")
	_, deleteErr := runDebugImageCommand(90*time.Second, "kubectl", "delete", "namespace", namespace,
		"--wait=true", "--timeout=80s", "--ignore-not-found")
	Expect(uninstallErr).NotTo(HaveOccurred())
	Expect(deleteErr).NotTo(HaveOccurred())
}

func startDebugPortForward(namespace, podName string) (int, func(), error) {
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

	stop := func() {
		cancel()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			_ = cmd.Process.Kill()
			<-done
		}
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

func inspectRemoteDebuggee(localPort int) (int, bool, error) {
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
	// Listing threads uses Delve's real headless API and proves both attach and
	// access to the paused manager process, not just an open TCP listener.
	if err := client.Call("RPCServer.ListThreads", struct{}{}, &reply); err != nil {
		return 0, false, err
	}
	return len(reply.Threads), state.Running, nil
}
