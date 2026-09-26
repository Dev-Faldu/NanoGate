/** "Try it" as a floating launcher (bottom-right) that opens a glass popover with the composer.
 *  The composer stays mounted while closed so an in-flight request keeps running. */
import { useEffect, useRef, useState } from "react";
import { Send, X } from "lucide-react";
import Composer, { type PlaygroundResult } from "./Composer";

export default function TryItPanel({ onResult }: { onResult?: (r: PlaygroundResult) => void }) {
  const [open, setOpen] = useState(false);
  const [unseen, setUnseen] = useState(false);
  const pop = useRef<HTMLElement>(null);
  const fab = useRef<HTMLButtonElement>(null);
  const openRef = useRef(open);
  openRef.current = open;

  useEffect(() => {
    if (!open) return;
    setUnseen(false);
    const t = setTimeout(() => pop.current?.querySelector<HTMLTextAreaElement>("#prompt")?.focus(), 120);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); fab.current?.focus(); } };
    const onDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!pop.current?.contains(target) && !fab.current?.contains(target)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { clearTimeout(t); window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);

  return (
    <>
      <section ref={pop} id="try-it" role="dialog" aria-labelledby="try-it-title" aria-hidden={!open} className="glass-pop" data-open={open}>
        <header className="flex items-start justify-between gap-4 px-6 pb-4 pt-5">
          <div>
            <h2 id="try-it-title" className="text-[16px] font-semibold tracking-tight text-ink">Send a real request</h2>
            <p className="mt-1 text-[13px] text-ink-3">Runs the full pipeline on the ZGX Nano. Nothing here is simulated.</p>
          </div>
          <button type="button" className="-mr-2 -mt-1 rounded-lg p-1.5 text-ink-3 transition-colors hover:bg-ink/5 hover:text-ink"
            onClick={() => setOpen(false)} aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="glass-rule" />
        <div className="glass-scroll p-6">
          <Composer onResult={(r) => { if (!openRef.current) setUnseen(true); onResult?.(r); }} />
        </div>
      </section>
      <button ref={fab} type="button" className="try-fab" aria-expanded={open} aria-controls="try-it"
        aria-label={open ? "Close request composer" : undefined} onClick={() => setOpen((o) => !o)}>
        {open ? <X className="h-5 w-5" /> : <><Send className="h-[18px] w-[18px]" />Try it</>}
        {unseen && !open && (
          <span className="absolute right-1 top-1 h-3 w-3 rounded-full border-2 border-ivory bg-mint" aria-label="New result" />
        )}
      </button>
    </>
  );
}
