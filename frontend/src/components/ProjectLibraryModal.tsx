import type { Lang } from "../appTypes";
import type { WorkflowSummary } from "../types";
import { StudioIcon } from "./StudioIcon";
import { ModalFrame } from "./PanelFrame";

type Props = {
  open: boolean;
  projects: WorkflowSummary[];
  loading: boolean;
  search: string;
  lang: Lang;
  copy: (zh: string, en: string) => string;
  onSearchChange: (value: string) => void;
  onOpen: (id: string) => void;
  onClose: () => void;
};

export function ProjectLibraryModal(props: Props) {
  if (!props.open) return null;

  return (
    <ModalFrame className="project-library-card" kicker="PROJECT LIBRARY" title={props.copy("打开项目", "Open Project")} closeLabel={props.copy("关闭", "Close")} onClose={props.onClose}>
        <div className="project-search-field">
          <StudioIcon name="search" />
          <input
            className="project-search"
            value={props.search}
            onChange={(event) => props.onSearchChange(event.target.value)}
            placeholder={props.copy("搜索项目", "Search projects")}
            aria-label={props.copy("搜索项目", "Search projects")}
            autoFocus
          />
        </div>
        <div className="project-list">
          {props.loading ? <div className="empty-hint">{props.copy("正在加载项目...", "Loading projects...")}</div> : null}
          {!props.loading && props.projects.length === 0 ? (
            <div className="empty-hint">{props.copy("没有找到已保存项目", "No saved projects found")}</div>
          ) : null}
          {props.projects.map((project) => (
            <button className="project-item" key={project.id} onClick={() => props.onOpen(project.id)}>
              <span className="project-item-main"><strong>{project.name}</strong><small>{project.id}</small></span>
              <span className="project-item-meta">
                <b>v{project.version}</b>
                <small>{project.updated_at ? new Date(project.updated_at).toLocaleString(props.lang) : "-"}</small>
              </span>
            </button>
          ))}
        </div>
    </ModalFrame>
  );
}
