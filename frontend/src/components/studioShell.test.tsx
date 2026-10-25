import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { EntityPalette } from "./EntityPalette";
import { StudioStatusRail } from "./StudioStatusRail";
import { Topbar } from "./Topbar";
import { StudioDock } from "./StudioDock";

describe("studio shell", () => {
  beforeEach(async () => { await i18n.changeLanguage("zh-CN"); });
  it("shows current product state, opens matching controls, and follows language changes", async () => {
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
    expect(screen.getByText("角色扮演")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByText("12 条事件")).toBeInTheDocument();
    fireEvent.click(screen.getByText("deepseek-chat"));
    fireEvent.click(screen.getByText("角色扮演"));
    fireEvent.click(screen.getByText("世界书"));
    expect(onModel).toHaveBeenCalledOnce();
    expect(onMode).toHaveBeenCalledOnce();
    expect(onBackground).toHaveBeenCalledOnce();

    await act(async () => { await i18n.changeLanguage("en-US"); });
    expect(screen.getByText("Roleplay")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("12 events")).toBeInTheDocument();
    expect(screen.getByText("World Book")).toBeInTheDocument();
  });

  it("renders preset entity counts and adds the chosen entity type", () => {
    const onAdd = vi.fn();
    render(<EntityPalette lang="zh-CN" counts={{ individual: 2, group: 1 }} onAdd={onAdd} />);

    expect(screen.getByText("个体")).toBeInTheDocument();
    expect(screen.getByText("群体")).toBeInTheDocument();
    fireEvent.click(screen.getByText("群体"));
    expect(onAdd).toHaveBeenCalledWith("group");
  });

  it("keeps project transfers in a dismissible menu and preserves their callbacks", () => {
    const onExport = vi.fn();
    const onImport = vi.fn();
    const noop = vi.fn();
    const { container } = render(<Topbar lang="zh-CN" workflowName="Test" isSaving={false} isStarting={false} isRunning={false} isStopping={false}
      onLangChange={noop} onWorkflowNameChange={noop} onSave={noop} onOpenProject={noop} onRun={noop} onStop={noop}
      onExport={onExport} onImport={onImport} />);
    const menu = container.querySelector("details")!;
    const trigger = screen.getByLabelText("项目操作");
    expect(trigger).toHaveAccessibleName("项目操作");
    menu.open = true;
    fireEvent.click(screen.getByRole("button", { name: "导出" }));
    expect(onExport).toHaveBeenCalledOnce();
    expect(menu.open).toBe(false);
    menu.open = true;
    const file = new File(['{"id":"wf_a"}'], "workflow.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("导入"), { target: { files: [file] } });
    expect(onImport).toHaveBeenCalledWith(file);
    expect(menu.open).toBe(false);
    menu.open = true;
    fireEvent.keyDown(document, { key: "Escape" });
    expect(menu.open).toBe(false);
    expect(trigger).toHaveFocus();
    menu.open = true;
    fireEvent.pointerDown(document.body);
    expect(menu.open).toBe(false);
  });

  it("keeps run controls unavailable during start and stop requests", () => {
    const noop = vi.fn();
    const props = { activePanel: "inspect" as const, isStarting: true, isRunning: false, isStopping: false,
      tr: (key: string) => i18n.t(key), onCreate: noop, onInspect: noop, onAdvanced: noop, onRun: noop, onStop: noop };
    const { rerender } = render(<StudioDock {...props} />);
    expect(screen.getByRole("button", { name: "启动中..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: /编辑所选/ })).toHaveAttribute("aria-pressed", "true");
    rerender(<StudioDock {...props} isStarting={false} isRunning isStopping />);
    expect(screen.getByRole("button", { name: "停止中..." })).toBeDisabled();
  });
});
