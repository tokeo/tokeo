# Sizing, resources and system planning

This guide shows how a tokeo app uses processes, threads, memory and
connections, which limits of the system it meets and how to check them before
a system goes live. It is a reference for planning, not a tuning recipe: the
numbers that count are always the ones measured on the own system.

.. note::

    The values marked as measured were taken on Linux with Python 3.13. They
    show the order of magnitude; measure the own app before relying on them.

## Principles

- Plan for the peak, not for the average: what runs at the same moment in the
  busiest minute decides the limits.
- Count threads, not processes. Linux counts every thread as a task with its
  own PID, so most limits below apply to threads.
- The lowest limit wins. A generous ```ulimit``` does not help when the
  container or the systemd unit allows fewer tasks.
- Measure first, then set limits, then measure again under load.

## What a tokeo app runs

Each service of an app is its own process tree. The table shows what one
service runs at most at the same moment and which setting controls it.

| Service | Processes | Threads per process | Setting, default |
|---|---|---|---|
| scheduler | 1 | jobs of the thread pool | ```scheduler.max_concurrent_jobs```, 10 |
| dramatiq | 1 master + ```worker_processes``` | ```worker_threads``` | tokeo: 2 processes, 1 thread; app template: 2 and 2 |
| smtpd | 1, or 1 master + ```pre_fork``` workers | one event loop | ```pre_fork```, ```max_connections```, ```max_processings``` (4) |
| nicegui, FastAPI | 1 | event loop + thread pool for ```def``` endpoints | anyio thread pool, 40 |
| grpc | 1 | thread pool | ```grpc.max_worker```, 1 |
| automate | 1 | one thread per host in parallel runs | the hosts of a task |
| ```@time_limit``` | | one timer thread per running call | |

More that adds to the count:

- dramatiq prefetches messages per worker process: ```queue_prefetch``` (two
  times the worker threads by default) and ```delay_queue_prefetch``` (a
  thousand times by default). Prefetched messages hold memory in the worker.
- Every thread that uses the diskcache holds its own SQLite connection, with
  the files of the database open, until the thread ends.
- Libraries start threads of their own, e.g. the event loop thread of the
  dramatiq AsyncIO middleware or the file watcher of a hot reload.

Count per service: processes × (threads + timer threads), summed over all
services and all apps on the system.

## Memory

| What | Measured |
|---|---|
| a typical service process after start | about 105 MB resident |
| of it, importing nicegui | about 37 MB |
| a waiting thread, resident | about 18 KB |
| a waiting thread, virtual (mostly its stack) | about 9 MB |

- Resident memory (RSS) is what is used, virtual memory (VmSize) is only
  reserved. A thread reserves its stack of 8 MB (```ulimit -s```), but uses
  only what it touches.
- Every process imports the extensions it loads. Fewer extensions per
  service, or imports inside the commands, keep every process smaller.
- Python keeps freed memory in its arenas; a peak stays visible in the RSS.
  ```MALLOC_ARENA_MAX=2``` limits the glibc arenas of processes with many
  threads.
- A limit on the data segment (```RLIMIT_DATA```) counts the thread stacks
  in full, 8 MB each. Linux enforces it since kernel 4.7, macOS does not.
- When a container or the system runs out of memory, the kernel kills a
  process (SIGKILL) without a trace in the app log. See below how to find it.

## Linux

| Limit | Applies to | Check | Note |
|---|---|---|---|
| ```kernel.pid_max``` | tasks (threads) of the whole system | ```cat /proc/sys/kernel/pid_max``` | 32768 on older or tuned systems, 4194304 on many current ones |
| ```kernel.threads-max``` | threads of the whole system | ```cat /proc/sys/kernel/threads-max``` | depends on the memory |
| ```ulimit -u``` | tasks per user | ```ulimit -u``` | all services of one user together |
| ```ulimit -n``` | open files and sockets per process | ```ulimit -n``` | often 1024 by default |
| cgroup ```pids.max``` | tasks of a container or unit | ```cat /sys/fs/cgroup/pids.max``` | the most frequent trap |
| systemd ```TasksMax``` | tasks of a service | ```systemctl show <unit> -p TasksMax -p TasksCurrent``` | a share of ```pid_max``` by default |
| ```vm.max_map_count``` | memory maps per process, 2 per thread | ```cat /proc/sys/vm/max_map_count``` | matters from about 30000 threads in one process |
| ```net.core.somaxconn``` | waiting connections per listening socket | ```cat /proc/sys/net/core/somaxconn``` | uvicorn asks for 2048 |
| ```net.ipv4.ip_local_port_range``` | outgoing connections to one target | ```cat /proc/sys/net/ipv4/ip_local_port_range``` | many short connections to one host |

Watch the load:

```sh
cat /proc/loadavg                 # 4th field: running/total tasks
ls /proc/<pid>/task | wc -l       # threads of a process
ps -eLf | wc -l                   # threads of the system
grep -E 'VmRSS|VmHWM|Threads' /proc/<pid>/status
```

## Containers (LXC, Docker)

- A container has its own limits for tasks (```pids.max```) and memory
  (```memory.max```), often lower than those of the host.
- An OOM kill inside a container shows up in its cgroup and in the kernel log
  of the host, not in the container:

  ```sh
  cat /sys/fs/cgroup/memory.events      # in the container: oom_kill count
  journalctl -k | grep -i 'killed process'   # on the host
  ```

- Processes of the same app often carry the same name; the kernel log names
  the process, so give services distinct names where possible.

## macOS

- macOS limits threads per process and per system:
  ```sysctl kern.num_threads kern.num_taskthreads```. The values differ by
  version, check them on the machine.
- Open files: ```ulimit -n``` (often 256 in a shell),
  ```sysctl kern.maxfiles kern.maxfilesperproc```, ```launchctl limit```.
- ```RLIMIT_DATA``` has no effect, a memory limit by it does not work.
- smtpd forks its workers only on Linux.

## Windows

Windows is not a target of tokeo and not tested.

## External systems

- RabbitMQ: each dramatiq worker process holds its connections and channels;
  the prefetch decides how many messages wait in memory per process.
- diskcache: one SQLite connection per thread, a few open files each. Many
  writers at the same moment wait on the lock of the database; keep work
  inside a transaction short.
- Databases and HTTP clients: size their pools to the threads that use them,
  not larger.
- Every connection and every open file uses a descriptor of ```ulimit -n```.

## Example: a loaded system

About 10000 jobs at the same moment on one host, all with ```@time_limit```:

| What | Estimate |
|---|---|
| worker threads | 10000 |
| timer threads | 10000 |
| tasks with processes and system | about 21000 |
| memory of the waiting timer threads | about 180 MB |
| memory of the processes | number of processes × about 105 MB, plus the work of the jobs |

Required: ```pid_max```, ```ulimit -u```, ```pids.max``` and ```TasksMax```
well above 21000. With ```pid_max = 32768``` the system is already near its
limit. When a limit is reached, starting a thread fails with
```RuntimeError: can't start new thread``` and the call fails.

## Checklist before going live

```sh
cat /proc/sys/kernel/pid_max /proc/sys/kernel/threads-max
ulimit -u; ulimit -n; ulimit -s
cat /sys/fs/cgroup/pids.max /sys/fs/cgroup/memory.max
systemctl show <unit> -p TasksMax -p MemoryMax
cat /proc/sys/net/core/somaxconn
```

- Sum the processes and threads of all services at their peak.
- Compare with the lowest of the limits above, keep a margin.
- Run the peak once on the target system and watch tasks and memory.
