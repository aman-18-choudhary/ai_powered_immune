import { Maximize2, Pause, Play, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { HelpCircle } from "lucide-react";
import { SPEED_OPTIONS } from "@/state/playback";
import { EXPLAIN } from "./notes";
import { cn } from "@/lib/utils/cn";

interface Props {
  status: string;
  speed: number;
  disabled?: boolean;
  stepKey: string | null;
  onToggle: () => void;
  onRestart: () => void;
  onSpeed: (s: number) => void;
  onFullscreen: () => void;
}

export function Controls({ status, speed, disabled, stepKey, onToggle, onRestart, onSpeed, onFullscreen }: Props) {
  const running = status === "running" || status === "starting" || status === "seeking";
  const finished = status === "finished" || status === "stopped" || status === "error";
  const explain = stepKey ? EXPLAIN[stepKey] : undefined;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant="primary"
        onClick={finished ? onRestart : onToggle}
        disabled={disabled}
        aria-label={finished ? "Play again" : running ? "Pause" : "Resume"}
        data-testid="play-toggle"
        className="min-w-[112px]"
      >
        {finished ? <RotateCcw className="h-4 w-4" aria-hidden /> : running ? <Pause className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
        {finished ? "Play again" : running ? "Pause" : "Resume"}
      </Button>
      <Button variant="secondary" onClick={onRestart} disabled={disabled} aria-label="Restart scenario" data-testid="restart">
        <RotateCcw className="h-4 w-4" aria-hidden /> Restart
      </Button>

      <div role="radiogroup" aria-label="Playback speed" className="ml-1 inline-flex items-center rounded-xl border border-line bg-chip p-0.5">
        {SPEED_OPTIONS.map((s) => (
          <button
            key={s}
            type="button"
            role="radio"
            aria-checked={Math.abs(speed - s) < 0.01}
            disabled={disabled}
            onClick={() => onSpeed(s)}
            className={cn(
              "mono rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors disabled:opacity-50",
              Math.abs(speed - s) < 0.01 ? "bg-raised text-fg shadow" : "text-muted hover:text-fg",
            )}
          >
            {s}x
          </button>
        ))}
      </div>

      <Popover>
        <PopoverTrigger asChild>
          <Button variant="ghost" size="sm" disabled={!explain} aria-label="Explain this step">
            <HelpCircle className="h-4 w-4" aria-hidden /> Explain this step
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[min(92vw,420px)]">
          {explain && (
            <div className="space-y-2.5 text-[13px] leading-snug">
              <p>{explain.what}</p>
              <p className="text-muted">
                <strong className="text-fg">Why it matters. </strong>
                {explain.why}
              </p>
              <p className="rounded-lg border border-line bg-chip px-2.5 py-1.5 text-xs text-muted">{explain.real}</p>
            </div>
          )}
        </PopoverContent>
      </Popover>

      <div className="ml-auto flex items-center gap-2">
        <span className="hidden text-[11px] text-subtle lg:inline">
          <kbd className="mono rounded border border-line px-1">Space</kbd> play/pause ·{" "}
          <kbd className="mono rounded border border-line px-1">←→</kbd> step ·{" "}
          <kbd className="mono rounded border border-line px-1">F</kbd> fullscreen
        </span>
        <Button variant="ghost" size="icon" onClick={onFullscreen} aria-label="Toggle fullscreen">
          <Maximize2 className="h-4 w-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}
