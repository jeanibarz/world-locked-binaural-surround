#!/usr/bin/env python3
"""make_device_pull.py — B2 / issue #104: one KookrPullTick advances EVERY active stream.

Idempotent patcher for the SYSVAD driver's pull mode. The B1 implementation gave each
CMiniportWaveRTStream its own pull thread waiting on the shared auto-reset event
`\\BaseNamedObjects\\KookrPullTick`; a synchronization event wakes only ONE waiter per SetEvent,
so with K streams in RUN (e.g. an app's render stream + our loopback capture) each advanced at
~1/K the tick rate — the capture starved and the output beeped (research-journal 72).

This moves the pull from per-stream to a single process-wide pull thread that, per tick, iterates a
device-wide list of active streams and calls PullAdvance() on each — so one tick = the whole device
advances one period, regardless of how many streams are running.

Edits EndpointsCommon/minwavertstream.{h,cpp} in place, preserving CRLF. Safe to re-run.
Usage:  py -3 make_device_pull.py <path-to>/EndpointsCommon
"""
import sys
from pathlib import Path

MARK = "B2 (issue #104)"


def load(p):
    raw = p.read_text(encoding="utf-8", errors="strict")
    return raw.replace("\r\n", "\n")


def save(p, text):
    p.write_text(text.replace("\n", "\r\n"), encoding="utf-8", newline="")


def must_replace(text, old, new, what):
    if old not in text:
        raise SystemExit(f"ANCHOR NOT FOUND ({what}): patcher cannot apply cleanly.")
    if text.count(old) != 1:
        raise SystemExit(f"ANCHOR NOT UNIQUE ({what}): found {text.count(old)}x.")
    return text.replace(old, new, 1)


# --------------------------------------------------------------------------- minwavertstream.h
def patch_h(p):
    t = load(p)
    if MARK in t:
        print("  .h already patched — skip")
        return
    # 1) add the list-entry member right after the B1 pull members
    t = must_replace(
        t,
        "    PKTHREAD                    m_pPullThread;\n",
        "    PKTHREAD                    m_pPullThread;\n"
        "    LIST_ENTRY                  m_PullListEntry;   // " + MARK + ": link in the device-wide active-pull list\n",
        "h:member",
    )
    # 2) add the register/unregister/init static declarations next to PullAdvance
    t = must_replace(
        t,
        "    // B1 pull-driven mode: advance one notification period (consume + notify), driven externally.\n"
        "    VOID PullAdvance();\n"
        "    static KSTART_ROUTINE PullThreadRoutine;\n",
        "    // B1 pull-driven mode: advance one notification period (consume + notify), driven externally.\n"
        "    VOID PullAdvance();\n"
        "    // " + MARK + ": one device-wide pull thread advances every registered stream per tick.\n"
        "    static VOID PullEnsureInit();\n"
        "    static VOID PullRegister(CMiniportWaveRTStream* stream);\n"
        "    static VOID PullUnregister(CMiniportWaveRTStream* stream);\n"
        "    static KSTART_ROUTINE PullThreadRoutine;\n",
        "h:decls",
    )
    save(p, t)
    print("  .h patched")


# ------------------------------------------------------------------------- minwavertstream.cpp
GLOBALS_AND_FUNCS = '''//=============================================================================
// ''' + MARK + ''': device-level single pull. One KookrPullTick advances EVERY active stream.
// A SYNCHRONIZATION (auto-reset) event wakes only ONE waiter per SetEvent, so the B1 per-stream pull
// threads stole ticks from each other (balance = 1/N streams; research-journal 72). Here a single
// process-wide pull thread waits on KookrPullTick and, per tick, advances all registered streams once.
// g_PullMutex is a KMUTEX (NOT a guarded/fast mutex): acquiring it stays at PASSIVE_LEVEL, which is
// required because register/unregister call PsCreateSystemThread / KeWaitForSingleObject(thread) --
// both PASSIVE-only. (A guarded/fast mutex raises to APC_LEVEL -> bugcheck.)
//
// Option B (DACL fix): the user-mode consumer CREATES KookrPullTick with a permissive (NULL) DACL; the
// driver only ever OPENS it (ZwOpenEvent + ObReferenceObjectByHandle). It never calls
// IoCreateSynchronizationEvent, which would create a SYSTEM-only-DACL object first whenever a Tablet
// stream entered RUN before the consumer started -- making the consumer's CreateEvent fail ACCESS_DENIED.
// The pull thread opens the event lazily and, whenever ticks stop for one watchdog period, releases and
// re-opens it, so it always re-locks onto the consumer's current event (e.g. across a consumer restart).
// ZwOpenEvent and ExEventObjectType are both declared by wdm.h (no forward declaration needed); under a
// non-HAL build ExEventObjectType is a POBJECT_TYPE* (deref with *ExEventObjectType when referencing it).
static KMUTEX           g_PullMutex;          // serialises register/unregister (PASSIVE_LEVEL)
static KSPIN_LOCK       g_PullSpinLock;       // guards the list vs the pull thread's iteration
static LIST_ENTRY       g_PullList;
static LONG             g_PullReady = 0;      // 0=uninit, 1=initializing, 2=ready
static ULONG            g_PullCount = 0;
static PKEVENT          g_pPullEvent = NULL;  // the single \\BaseNamedObjects\\KookrPullTick
static HANDLE           g_hPullEvent = NULL;
static KEVENT           g_PullStopEvent;
static PKTHREAD         g_pPullThread = NULL;

VOID CMiniportWaveRTStream::PullEnsureInit()
{
    if (InterlockedCompareExchange(&g_PullReady, 1, 0) == 0)
    {
        KeInitializeMutex(&g_PullMutex, 0);
        KeInitializeSpinLock(&g_PullSpinLock);
        InitializeListHead(&g_PullList);
        KeInitializeEvent(&g_PullStopEvent, NotificationEvent, FALSE);
        InterlockedExchange(&g_PullReady, 2);
    }
    else
    {
        while (InterlockedCompareExchange(&g_PullReady, 2, 2) != 2) { /* brief spin until ready */ }
    }
}

VOID CMiniportWaveRTStream::PullRegister(CMiniportWaveRTStream* stream)
{
    PullEnsureInit();
    KeWaitForSingleObject(&g_PullMutex, Executive, KernelMode, FALSE, NULL);
    KIRQL irql;
    KeAcquireSpinLock(&g_PullSpinLock, &irql);
    BOOLEAN first = FALSE;
    if (stream->m_PullListEntry.Flink == NULL)   // not already linked (idempotent)
    {
        InsertTailList(&g_PullList, &stream->m_PullListEntry);
        g_PullCount++;
        first = (g_PullCount == 1);
    }
    KeReleaseSpinLock(&g_PullSpinLock, irql);
    (VOID)first;
    // Start the single pull thread on the first register (or if a prior start failed). The thread opens
    // KookrPullTick lazily (Option B: open-only, never create) and free-runs on the 100 ms watchdog until
    // the consumer creates it -- so a Tablet stream that enters RUN before the consumer still advances.
    if (g_PullCount >= 1 && g_pPullThread == NULL)
    {
        KeClearEvent(&g_PullStopEvent);
        HANDLE hThread = NULL;
        NTSTATUS st = PsCreateSystemThread(&hThread, THREAD_ALL_ACCESS, NULL, NULL, NULL,
                                           CMiniportWaveRTStream::PullThreadRoutine, NULL);
        if (NT_SUCCESS(st))
        {
            ObReferenceObjectByHandle(hThread, THREAD_ALL_ACCESS, *PsThreadType, KernelMode,
                                      (PVOID*)&g_pPullThread, NULL);
            ZwClose(hThread);
        }
        // If PsCreateSystemThread failed, g_pPullThread stays NULL and the next register retries.
    }
    KeReleaseMutex(&g_PullMutex, FALSE);
}

VOID CMiniportWaveRTStream::PullUnregister(CMiniportWaveRTStream* stream)
{
    PullEnsureInit();
    KeWaitForSingleObject(&g_PullMutex, Executive, KernelMode, FALSE, NULL);
    KIRQL irql;
    KeAcquireSpinLock(&g_PullSpinLock, &irql);
    if (stream->m_PullListEntry.Flink != NULL)   // linked -> remove (idempotent)
    {
        RemoveEntryList(&stream->m_PullListEntry);
        stream->m_PullListEntry.Flink = NULL;
        stream->m_PullListEntry.Blink = NULL;
        if (g_PullCount > 0) { g_PullCount--; }
    }
    BOOLEAN last = (g_PullCount == 0);
    KeReleaseSpinLock(&g_PullSpinLock, irql);
    if (last && g_pPullThread != NULL)
    {
        KeSetEvent(&g_PullStopEvent, 0, FALSE);
        KeWaitForSingleObject(g_pPullThread, Executive, KernelMode, FALSE, NULL);
        ObDereferenceObject(g_pPullThread);
        g_pPullThread = NULL;
        // Option B: the pull thread released KookrPullTick on its way out (PullReleaseEvent), so
        // g_pPullEvent / g_hPullEvent are already cleared -- the event's lifetime is owned by the thread.
        KeClearEvent(&g_PullStopEvent);
    }
    KeReleaseMutex(&g_PullMutex, FALSE);
}

'''

PULLTHREAD_OLD = '''VOID CMiniportWaveRTStream::PullThreadRoutine(_In_ PVOID StartContext)
{
    CMiniportWaveRTStream* _this = (CMiniportWaveRTStream*)StartContext;
    PVOID objs[2];
    objs[0] = _this->m_pPullEvent;          // user-mode pseudo-K11 tick
    objs[1] = &_this->m_PullStopEvent;      // shutdown
    LARGE_INTEGER timeout;
    timeout.QuadPart = (-100) * ((LONGLONG)HNSTIME_PER_MILLISECOND);  // 100 ms free-run watchdog
    for (;;)
    {
        NTSTATUS st = KeWaitForMultipleObjects(2, objs, WaitAny, Executive, KernelMode, FALSE,
                                               &timeout, NULL);
        if (st == STATUS_WAIT_0 + 1) { break; }     // stop requested
        _this->PullAdvance();                       // tick (WAIT_0) or timeout free-run
    }
    PsTerminateSystemThread(STATUS_SUCCESS);
}'''

PULLTHREAD_NEW = '''// ''' + MARK + ''': open KookrPullTick (created by the user-mode consumer with a permissive DACL) --
// open-only, never create. Called only from the pull thread, so writes to g_pPullEvent/g_hPullEvent
// are single-writer.
static VOID PullTryOpenEvent()
{
    if (g_pPullEvent != NULL) { return; }
    UNICODE_STRING evName;
    RtlInitUnicodeString(&evName, L"\\\\BaseNamedObjects\\\\KookrPullTick");
    OBJECT_ATTRIBUTES oa;
    InitializeObjectAttributes(&oa, &evName, OBJ_KERNEL_HANDLE | OBJ_CASE_INSENSITIVE, NULL, NULL);
    HANDLE h = NULL;
    NTSTATUS os = ZwOpenEvent(&h, SYNCHRONIZE, &oa);   // SYNCHRONIZE: we only WAIT; the consumer sets it
    if (!NT_SUCCESS(os)) { return; }                   // not created yet -> stay on the free-run watchdog
    PVOID pObj = NULL;
    if (NT_SUCCESS(ObReferenceObjectByHandle(h, SYNCHRONIZE, *ExEventObjectType, KernelMode, &pObj, NULL)))
    {
        g_pPullEvent = (PKEVENT)pObj;
        g_hPullEvent = h;
    }
    else
    {
        ZwClose(h);
    }
}

// ''' + MARK + ''': release our reference to KookrPullTick so the next PullTryOpenEvent re-acquires the
// consumer's CURRENT event. We hold BOTH an object reference and a handle, so release both. Thread-only.
static VOID PullReleaseEvent()
{
    if (g_pPullEvent) { ObDereferenceObject(g_pPullEvent); g_pPullEvent = NULL; }
    if (g_hPullEvent) { ZwClose(g_hPullEvent); g_hPullEvent = NULL; }
}

VOID CMiniportWaveRTStream::PullThreadRoutine(_In_ PVOID StartContext)
{
    UNREFERENCED_PARAMETER(StartContext);
    LARGE_INTEGER timeout;
    timeout.QuadPart = (-100) * ((LONGLONG)HNSTIME_PER_MILLISECOND);  // 100 ms free-run watchdog
    for (;;)
    {
        PullTryOpenEvent();   // lock onto the consumer's event once it exists (open-only, never create)

        if (g_pPullEvent != NULL)
        {
            PVOID objs[2];
            objs[0] = g_pPullEvent;        // the single KookrPullTick (auto-reset)
            objs[1] = &g_PullStopEvent;    // shutdown
            NTSTATUS st = KeWaitForMultipleObjects(2, objs, WaitAny, Executive, KernelMode, FALSE,
                                                   &timeout, NULL);
            if (st == STATUS_WAIT_0 + 1) { break; }    // stop requested
            if (st == STATUS_TIMEOUT)
            {
                // No tick for a full watchdog period: the consumer is gone or was restarted. Drop our ref
                // so the next PullTryOpenEvent re-locks onto its current event instead of a stale one.
                PullReleaseEvent();
            }
            // STATUS_WAIT_0 (tick) or STATUS_TIMEOUT (watchdog) both advance the device one period below.
        }
        else
        {
            NTSTATUS st = KeWaitForSingleObject(&g_PullStopEvent, Executive, KernelMode, FALSE, &timeout);
            if (st == STATUS_SUCCESS) { break; }       // stop requested (STATUS_TIMEOUT -> free-run advance)
        }
        // ''' + MARK + ''': one tick (or watchdog) advances EVERY active stream once -> whole device moves a period.
        KIRQL irql;
        KeAcquireSpinLock(&g_PullSpinLock, &irql);
        for (PLIST_ENTRY le = g_PullList.Flink; le != &g_PullList; le = le->Flink)
        {
            CMiniportWaveRTStream* s = CONTAINING_RECORD(le, CMiniportWaveRTStream, m_PullListEntry);
            s->PullAdvance();
        }
        KeReleaseSpinLock(&g_PullSpinLock, irql);
    }
    PullReleaseEvent();   // own the event lifetime: always release before the thread exits
    PsTerminateSystemThread(STATUS_SUCCESS);
}'''

RUN_OLD = '''                UNICODE_STRING evName;
                RtlInitUnicodeString(&evName, L"\\\\BaseNamedObjects\\\\KookrPullTick");
                // The user-mode consumer creates this named event first (with a permissive DACL);
                // IoCreateSynchronizationEvent OPENS the existing one. Its pointer is owned by the
                // handle - do NOT ObDereferenceObject it (that over-dereferences -> bugcheck 0x18).
                m_pPullEvent = IoCreateSynchronizationEvent(&evName, &m_hPullEvent);
                if (m_pPullEvent != NULL)
                {
                    HANDLE hThread = NULL;
                    NTSTATUS st = PsCreateSystemThread(&hThread, THREAD_ALL_ACCESS, NULL, NULL, NULL,
                                                       CMiniportWaveRTStream::PullThreadRoutine, this);
                    if (NT_SUCCESS(st))
                    {
                        ObReferenceObjectByHandle(hThread, THREAD_ALL_ACCESS, *PsThreadType, KernelMode,
                                                  (PVOID*)&m_pPullThread, NULL);
                        ZwClose(hThread);
                    }
                }'''

RUN_NEW = '''                // ''' + MARK + ''': register with the device-wide pull list (one shared pull thread
                // advances every active stream per tick, instead of a per-stream thread that would
                // steal ticks from the others on the shared auto-reset event).
                CMiniportWaveRTStream::PullRegister(this);'''

PAUSE_OLD = '''                if (m_bPullMode && m_pPullThread != NULL)
                {
                    // B1: stop the pull thread cleanly.
                    KeSetEvent(&m_PullStopEvent, 0, FALSE);
                    KeWaitForSingleObject(m_pPullThread, Executive, KernelMode, FALSE, NULL);
                    ObDereferenceObject(m_pPullThread);
                    m_pPullThread = NULL;
                    // pointer owned by the handle; close the handle only (no deref -> no 0x18).
                    if (m_hPullEvent) { ZwClose(m_hPullEvent); m_hPullEvent = NULL; }
                    m_pPullEvent = NULL;
                    KeClearEvent(&m_PullStopEvent);
                }'''

PAUSE_NEW = '''                if (m_bPullMode)
                {
                    // ''' + MARK + ''': leave the device-wide pull list (tears down the shared thread if last).
                    CMiniportWaveRTStream::PullUnregister(this);
                }'''

DTOR_OLD = '''    // B1: defensive pull-thread teardown (normally done on the RUN->PAUSE transition).
    if (m_pPullThread != NULL)
    {
        KeSetEvent(&m_PullStopEvent, 0, FALSE);
        KeWaitForSingleObject(m_pPullThread, Executive, KernelMode, FALSE, NULL);
        ObDereferenceObject(m_pPullThread);
        m_pPullThread = NULL;
    }
    if (m_hPullEvent) { ZwClose(m_hPullEvent); m_hPullEvent = NULL; }
    m_pPullEvent = NULL;   // pointer owned by the handle; closing the handle is enough'''

DTOR_NEW = '''    // ''' + MARK + ''': defensive unregister from the device-wide pull list (idempotent).
    PullUnregister(this);'''

CTOR_OLD = '''    m_pPullThread = NULL;
    KeInitializeEvent(&m_PullStopEvent, NotificationEvent, FALSE);'''

CTOR_NEW = '''    m_pPullThread = NULL;
    KeInitializeEvent(&m_PullStopEvent, NotificationEvent, FALSE);
    m_PullListEntry.Flink = NULL;   // ''' + MARK + ''': not yet in the device pull list
    m_PullListEntry.Blink = NULL;'''


def patch_cpp(p):
    t = load(p)
    if MARK in t:
        print("  .cpp already patched — skip")
        return
    t = must_replace(t, CTOR_OLD, CTOR_NEW, "cpp:ctor")
    t = must_replace(t, DTOR_OLD, DTOR_NEW, "cpp:dtor")
    t = must_replace(t, RUN_OLD, RUN_NEW, "cpp:run")
    t = must_replace(t, PAUSE_OLD, PAUSE_NEW, "cpp:pause")
    # insert globals + funcs immediately before the (soon-rewritten) PullThreadRoutine
    t = must_replace(t, PULLTHREAD_OLD, GLOBALS_AND_FUNCS + PULLTHREAD_NEW, "cpp:thread+globals")
    save(p, t)
    print("  .cpp patched")


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: make_device_pull.py <path-to>/EndpointsCommon")
    d = Path(argv[1])
    patch_h(d / "minwavertstream.h")
    patch_cpp(d / "minwavertstream.cpp")
    print("done.")


if __name__ == "__main__":
    main(sys.argv)
