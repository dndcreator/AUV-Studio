import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { ReactFlowProvider } from "reactflow";
import i18n from "../i18n";
import { SimulationNode } from "./SimulationNode";

const props = { id: "a", type: "default", selected: false, dragging: false, isConnectable: true, xPos: 0, yPos: 0, zIndex: 0 };

describe("simulation node", () => {
  beforeEach(async () => { await i18n.changeLanguage("zh-CN"); });

  it("keeps connection handles and shows translated runtime status", async () => {
    const { container } = render(<ReactFlowProvider><SimulationNode {...props} data={{ label: "林夏", config: { entity_type: "individual" }, displayStatus: "running" }} /></ReactFlowProvider>);
    expect(screen.getByText("林夏")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(container.querySelectorAll(".react-flow__handle")).toHaveLength(2);
    expect(container.querySelector(".simulation-node-symbol")).toBeInTheDocument();
    await act(async () => { await i18n.changeLanguage("en-US"); });
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("does not impose a preset entity symbol on custom technical nodes", () => {
    const { container } = render(<ReactFlowProvider><SimulationNode {...props} data={{ label: "My agent", nodeType: "external_agent" }} /></ReactFlowProvider>);
    expect(screen.getByText("My agent")).toBeInTheDocument();
    expect(screen.getByText("待机")).toBeInTheDocument();
    expect(container.querySelector(".simulation-node-symbol")).toBeNull();
  });
});
