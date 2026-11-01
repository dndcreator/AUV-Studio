import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DrawerFrame, ModalFrame } from "./PanelFrame";

describe("panel frames", () => {
  it("separates modal controls from the scrollable content and restores focus", () => {
    const trigger = document.createElement("button");
    document.body.append(trigger);
    trigger.focus();
    const close = vi.fn();
    const { unmount } = render(<ModalFrame title="Model Setup" closeLabel="Close" onClose={close}>
      <label>Model<input /></label><button>Save</button>
    </ModalFrame>);
    const dialog = screen.getByRole("dialog", { name: "Model Setup" });
    expect(dialog).toHaveFocus();
    expect(screen.getByRole("heading").closest(".modal-body")).toBeNull();
    expect(screen.getByLabelText("Model").closest(".modal-body")).not.toBeNull();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByText("Close")).toHaveFocus();
    screen.getByText("Save").focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByText("Close")).toHaveFocus();
    fireEvent.click(screen.getByLabelText("Model"));
    expect(close).not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(close).toHaveBeenCalledOnce();
    unmount();
    expect(trigger).toHaveFocus();
    trigger.remove();
  });

  it("only dismisses the topmost nested modal", () => {
    const outerClose = vi.fn();
    const innerClose = vi.fn();
    render(<ModalFrame title="Result" closeLabel="Close Result" onClose={outerClose}>
      <ModalFrame title="Media" closeLabel="Close Media" onClose={innerClose}><input /></ModalFrame>
    </ModalFrame>);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(innerClose).toHaveBeenCalledOnce();
    expect(outerClose).not.toHaveBeenCalled();
  });

  it("keeps drawer title, badge and close control outside the scrolling body", () => {
    const close = vi.fn();
    const { container, rerender } = render(<DrawerFrame open title="Create" className="left" badge="Director" closeLabel="Close" onClose={close}>
      <label>Objective<textarea /></label>
    </DrawerFrame>);
    expect(screen.getByRole("complementary", { name: "Create" })).toHaveClass("open");
    expect(screen.getByRole("heading").closest(".drawer-body")).toBeNull();
    expect(screen.getByLabelText("Objective").closest(".drawer-body")).not.toBeNull();
    fireEvent.click(screen.getByLabelText("Close"));
    expect(close).toHaveBeenCalledOnce();
    rerender(<DrawerFrame open={false} title="Create" className="left" closeLabel="Close" onClose={close}>Body</DrawerFrame>);
    expect(container.querySelector("aside")).toHaveAttribute("aria-hidden", "true");
  });
});
