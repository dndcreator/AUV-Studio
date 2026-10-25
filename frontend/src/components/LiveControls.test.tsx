import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LiveControls } from "./LiveControls";
import i18n from "../i18n";

describe("Live controls", () => {
  it("separates playback selection from opening the full transcript and follows locale", async () => {
    await i18n.changeLanguage("en-US");
    const change = vi.fn();
    const open = vi.fn();
    const parent = vi.fn();
    const { rerender } = render(<div onClick={parent}><LiveControls mode="sync" status="running" onChange={change} onOpen={open} /></div>);
    fireEvent.click(screen.getByRole("button", { name: "Live" }));
    expect(change).toHaveBeenCalledWith("paced");
    expect(open).not.toHaveBeenCalled();
    expect(parent).not.toHaveBeenCalled();
    rerender(<LiveControls mode="paced" status="running" onChange={change} onOpen={open} />);
    expect(screen.getByRole("button", { name: "Paced" })).toHaveAttribute("aria-pressed", "true");
    await act(async () => { await i18n.changeLanguage("zh-CN"); });
    expect(screen.getByRole("button", { name: "演播" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: i18n.t("liveBroadcast") }));
    expect(open).toHaveBeenCalledOnce();
  });
});
