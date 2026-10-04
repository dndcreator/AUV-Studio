import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EntityPalette } from "./EntityPalette";
import { StudioStatusRail } from "./StudioStatusRail";

describe("studio shell", () => {
  it("shows current product state and opens the matching controls", () => {
    const onModel = vi.fn();
    const onMode = vi.fn();
    const onBackground = vi.fn();
    render(
      <StudioStatusRail
        copy={(zh) => zh}
        modelReady
        modelName="deepseek-chat"
        mode="roleplay"
        entityCount={4}
        backgroundCount={3}
        runStatus="running"
        eventCount={12}
        onModel={onModel}
        onMode={onMode}
        onBackground={onBackground}
      />
    );

    expect(screen.getByText("deepseek-chat")).toBeInTheDocument();
    expect(screen.getByText("roleplay")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    fireEvent.click(screen.getByText("deepseek-chat"));
    fireEvent.click(screen.getByText("roleplay"));
    fireEvent.click(screen.getByText("WORLD BOOK"));
    expect(onModel).toHaveBeenCalledOnce();
    expect(onMode).toHaveBeenCalledOnce();
    expect(onBackground).toHaveBeenCalledOnce();
  });

  it("renders preset entity counts and adds the chosen entity type", () => {
    const onAdd = vi.fn();
    render(<EntityPalette lang="zh-CN" counts={{ individual: 2, group: 1 }} onAdd={onAdd} />);

    expect(screen.getByText("个体")).toBeInTheDocument();
    expect(screen.getByText("群体")).toBeInTheDocument();
    fireEvent.click(screen.getByText("群体"));
    expect(onAdd).toHaveBeenCalledWith("group");
  });
});
