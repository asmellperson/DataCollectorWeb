import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "../services/api";
import { inspectionBase, recordingBase, saveInspection } from "../services/securityAnnotation";
import type { InspectionContent, InspectionDetail, InspectionRecording } from "../types/securityAnnotation";

export function useSecurityAnnotation(notify: (message: string) => void) {
  const [recordings, setRecordings] = useState<InspectionRecording[]>([]);
  const [detail, setDetail] = useState<InspectionDetail | null>(null);
  const [draft, setDraft] = useState<InspectionContent>({ persons: [], rounds: [] });
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const current = useRef({ id: "", dirty: false, busy: false, sequence: 0, revision: -1 });
  const review = useRef({ seconds: 0, last: Date.now(), activity: 0 });
  const mounted = useRef(true);

  const adopt = useCallback((value: InspectionDetail) => {
    setDetail(value);
    setDraft({ persons: value.annotation.persons, rounds: value.annotation.rounds });
    setDirty(false);
    current.current.dirty = false;
    current.current.revision = value.annotation.revision;
    review.current = { seconds: 0, last: Date.now(), activity: 0 };
  }, []);

  const refresh = useCallback(async (force = false) => {
    const id = current.current.id;
    const sequence = current.current.sequence;
    if (!id) return;
    const value = await api<InspectionDetail>(recordingBase(id), { cache: "no-store" });
    if (!mounted.current || current.current.id !== id || current.current.sequence !== sequence) return;
    if (value.annotation.revision < current.current.revision) return;
    if (force || (!current.current.dirty && !current.current.busy)) {
      if (force || current.current.revision !== value.annotation.revision) adopt(value);
      else setDetail(previous => previous ? { ...previous, recording: value.recording, latest_draft: value.latest_draft } : value);
    } else {
      setDetail(previous => previous ? { ...previous, recording: value.recording, latest_draft: value.latest_draft } : previous);
    }
  }, [adopt]);

  const select = useCallback(async (id: string) => {
    if (current.current.busy) return;
    if (current.current.dirty && !confirm("当前有未保存修改，放弃修改并切换录像？")) return;
    const sequence = ++current.current.sequence;
    current.current.id = id;
    current.current.revision = -1;
    setDetail(null);
    setDirty(false);
    current.current.dirty = false;
    try {
      const value = await api<InspectionDetail>(recordingBase(id), { cache: "no-store" });
      if (mounted.current && sequence === current.current.sequence) adopt(value);
    } catch (error) {
      if (sequence === current.current.sequence) notify(errorText(error));
    }
  }, [adopt, notify]);

  useEffect(() => {
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    async function tick() {
      try {
        const values = await api<InspectionRecording[]>(`${inspectionBase}/recordings`, { cache: "no-store" });
        if (stopped) return;
        setRecordings(values);
        if (current.current.id) await refresh();
      } catch (error) {
        if (!stopped) notify(errorText(error));
      }
      if (!stopped) timer = setTimeout(tick, 2500);
    }
    void tick();
    const count = setInterval(() => {
      const now = Date.now();
      if (!document.hidden && current.current.id && !current.current.busy && now - review.current.activity < 60000)
        review.current.seconds += Math.min((now - review.current.last) / 1000, 2);
      review.current.last = now;
    }, 1000);
    return () => { stopped = true; mounted.current = false; clearTimeout(timer); clearInterval(count); };
  }, [refresh, notify]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function change(value: InspectionContent) {
    if (current.current.busy) return;
    current.current.dirty = true;
    setDirty(true);
    setDraft(value);
  }
  function activity() { review.current.activity = Date.now(); }
  async function save(content = draft) {
    if (!detail || current.current.busy) return;
    current.current.busy = true;
    setBusy(true);
    const seconds = Math.round(review.current.seconds * 1000) / 1000;
    try {
      const annotation = await saveInspection(detail.recording.id, detail.annotation, content, seconds);
      if (!mounted.current) return;
      adopt({ ...detail, annotation });
    } catch (error) {
      notify(errorText(error));
    } finally {
      current.current.busy = false;
      if (mounted.current) setBusy(false);
    }
  }
  return { recordings, detail, draft, dirty, busy, change, save, select, refresh, activity };
}
