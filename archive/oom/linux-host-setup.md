# Raspberry Pi 5 Linux Host Setup

This project was validated on a Raspberry Pi 5 running a Linux host with Docker and cgroup v2.

## Why cgroup memory support matters

Docker enforces a container memory limit through the Linux cgroup memory controller. Initially, the Pi exposed cgroup v2 but did not list the memory controller:

~~~sh
cat /sys/fs/cgroup/cgroup.controllers
~~~

Without memory in that output, Docker reported no memory-limit support and the demo completed a 1 GiB allocation despite its configured 64 MiB limit.

## Enable the memory controller

On this Pi, append the following parameter to the existing single line in the boot command-line file:

~~~sh
sudo nano /boot/firmware/cmdline.txt
~~~

~~~text
cgroup_enable=memory
~~~

Keep the file on one line and preserve the existing parameters. The original command line included cgroup_disable=memory; appending cgroup_enable=memory made the memory controller available after rebooting.

~~~sh
sudo reboot
~~~

## Verify the setup

After reconnecting, confirm that the memory controller is available:

~~~sh
cat /sys/fs/cgroup/cgroup.controllers
~~~

The output must include memory. Also inspect Docker for cgroup or memory warnings:

~~~sh
sudo docker info 2>&1 | grep -iE 'WARNING|cgroup|memory'
~~~

## Verify the project

Clone the repository and run the original Python OOM exercise. The default
Compose workload is separate, so select the archived OOM file explicitly:

~~~sh
git clone git@github.com:sohampachpande/container-runtime-visibility.git
cd container-runtime-visibility
docker compose -p container-runtime-visibility -f archive/oom/compose.yaml up --build --detach
curl --fail --silent --show-error http://localhost:8080/healthz
curl --fail --silent --show-error --max-time 20 'http://localhost:8080/allocate?mb=1024'
~~~

The allocation request should receive an empty reply because the process is killed. Confirm the diagnosis:

~~~sh
docker inspect container-runtime-visibility-test-app-1 \
  --format 'Running={{.State.Running}} OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}}'
~~~

Expected output:

~~~text
Running=false OOMKilled=true ExitCode=137
~~~

## Next eBPF preflight

Before building the eBPF observer, verify BTF metadata and supported BPF features:

~~~sh
test -r /sys/kernel/btf/vmlinux && echo "BTF available"
sudo bpftool feature probe kernel
~~~
