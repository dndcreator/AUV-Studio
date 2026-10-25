import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EvidenceOverlay, LiveTranscriptOverlay, RunResultOverlay } from "./RunOverlays";

const copy = (zh: string) => zh;

describe("run overlays", () => {
  it("keeps evidence selection behind explicit callbacks", () => {
    const onUse = vi.fn();
    const onSkip = vi.fn();
    render(
      <EvidenceOverlay
        open
        pending={false}
        evidence={{
          version: 1,
          package_name: "pack-1",
          items: [{ id: "item-1", name: "survey.csv", kind: "table", content: "" }],
          skipped: [],
          errors: [],
          summary: { parsed_files: 1, skipped_files: 0, failed_files: 0, characters: 0 },
        }}
        copy={copy}
        onFile={vi.fn()}
        onUse={onUse}
        onSkip={onSkip}
      />
    );

    fireEvent.click(screen.getByText("确认使用"));
    expect(onUse).toHaveBeenCalledOnce();
    expect(onSkip).not.toHaveBeenCalled();
  });

  it("renders live and result overlays as independent views", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <LiveTranscriptOverlay
        open
        rows={[{ seq: 1, speaker: "Alice", text: "Hello", time: "10:00", event: "dialogue", score: 1 }]}
        finalText="Final scene"
        copy={copy}
        onClose={onClose}
      />
    );
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("Final scene")).toBeInTheDocument();

    rerender(
      <RunResultOverlay
        open
        runId={null}
        finalText="Result text"
        report={null}
        reportPending={false}
        copy={copy}
        onClose={onClose}
        onOpenTranscript={vi.fn()}
        onGenerateReport={vi.fn()}
      />
    );
    expect(screen.getByText("Result text")).toBeInTheDocument();
    expect(screen.getByText("生成正式汇报")).toBeDisabled();
  });
});
