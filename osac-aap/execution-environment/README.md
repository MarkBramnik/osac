# OSAC AAP execution environment

Tools and configuration to run playbooks that interact with both OpenStack/ESI and OpenShift.

## Building the execution environment

1. Update `requirements.txt` from `pyproject.toml` in the top directory:

    ```
    uv pip compile ../pyproject.toml > requirements.txt
    ```

2. Build the execution environment:

   Run the following commands from the `osac-aap/` directory:

    ```make
    make execution-environment-build
    ```

   By default, the build uses the container engine's native platform. Set
   `EE_PLATFORM` to override it, for example when building on Apple Silicon for
   an x86_64 cluster:

    ```
    make execution-environment-build EE_PLATFORM=linux/amd64 \
      IMG=ghcr.io/<your-registry>/osac-aap:<tag>
    make execution-environment-push IMG=ghcr.io/<your-registry>/osac-aap:<tag>
    ```

   `EE_PLATFORM` is passed to Ansible Builder as the container engine's
   `--platform` option. The push target publishes the image produced by the
   build; it does not select or change its platform. Cross-platform builds may
   require emulation support in the container engine.
