import type { Lang } from "../appTypes";
import type { WorkflowSummary } from "../types";

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
    <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={props.onClose}>
      <div className="modal-card project-library-card" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div>
            <span className="result-kicker">PROJECT LIBRARY</span>
            <h2>{props.copy("打开项目", "Open Project")}</h2>
          </div>
          <button className="btn" onClick={props.onClose}>{props.copy("关闭", "Close")}</button>
        </div>
        <input
          className="project-search"
          value={props.search}
          onChange={(event) => props.onSearchChange(event.target.value)}
          placeholder={props.copy("搜索项目", "Search projects")}
          autoFocus
        />
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
      </div>
    </div>
  );
}
