// This file holds code only, it is shown by POCKETBASE.md of tokeo-pocketbase.
// Copy it as pb_hooks/realtime.pb.js next to pb_data of PocketBase.
//
// Shorter realtime connections: PocketBase ends a realtime connection without
// events after 5 minutes and any one after 30 minutes. This hook sets both
// times from the environment, in seconds, else from the values below.

onRealtimeConnectRequest((e) => {
    // durations of PocketBase are nanoseconds; the handler runs on its own,
    // so everything it needs is defined inside
    const second = 1000 * 1000 * 1000
    const idle = Number($os.getenv("PB_REALTIME_IDLE_SECONDS") || 120)
    const max = Number($os.getenv("PB_REALTIME_MAX_SECONDS") || 600)
    e.idleTimeout = idle * second
    e.maxTimeout = max * second
    e.next()
})
