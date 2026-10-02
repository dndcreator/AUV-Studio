import type { NodeType } from "./types";

export type EntityType = "individual" | "group" | "organization" | "environment" | "event" | "artifact" | "human_gate";

export type EntityDefinition = {
  type: EntityType;
  titleZh: string;
  titleEn: string;
  descriptionZh: string;
  descriptionEn: string;
  nodeType: NodeType;
  defaultName: string;
  defaultProfile: string;
  behaviorPrompt: string;
};

export const entityDefinitions: EntityDefinition[] = [
  {
    type: "individual",
    titleZh: "个体",
    titleEn: "Individual",
    descriptionZh: "具体的人或角色，负责主观反应、行动、说话、记忆和决策。",
    descriptionEn: "A concrete person or character that speaks, acts, remembers, and decides subjectively.",
    nodeType: "agent",
    defaultName: "新个体",
    defaultProfile: "身份、目标、性格、当前状态和边界。",
    behaviorPrompt:
      "You are simulating one concrete individual. React subjectively based on identity, goals, personality, memory, and environment. You may speak, act, hesitate, misjudge, hide information, regret, and change your stance. Do not represent an entire group unless the profile explicitly says so. Few-shot: when questioned, the individual may pause, remember a painful detail, answer defensively with concrete words, leave the room, send a message, lie, reconcile, retaliate, or make an impulsive choice."
  },
  {
    type: "group",
    titleZh: "群体",
    titleEn: "Group",
    descriptionZh: "一批人或舆论氛围，负责群体反应、分歧、传闻和社会压力。",
    descriptionEn: "A collective atmosphere or population segment that produces opinions, rumors, pressure, and subgroup splits.",
    nodeType: "agent",
    defaultName: "新群体",
    defaultProfile: "群体画像、规模、分层变量、主流观点、少数观点和群体规范。",
    behaviorPrompt:
      "You are simulating a group, not a single person. Produce collective mood, majority view, minority views, typical quotes, behavior tendencies, rumors, and social pressure. Do not develop one stable private personality, one-to-one romance, or intimate personal memory. Few-shot: the office grows tense, several people whisper in the break room, a rumor spreads, most people avoid taking sides, and a small subgroup becomes openly hostile."
  },
  {
    type: "organization",
    titleZh: "组织",
    titleEn: "Organization",
    descriptionZh: "公司、团队、军队或机构，负责目标、资源、权力结构和制度化决策。",
    descriptionEn: "A company, team, army, or institution with goals, resources, structure, and institutional decisions.",
    nodeType: "agent",
    defaultName: "新组织",
    defaultProfile: "组织目标、权力结构、资源、规则、内部分歧和当前策略。",
    behaviorPrompt:
      "You are simulating an organization with structure, resources, rules, and goals. Output organizational stance, decisions, task assignments, resource changes, internal disagreement, and external strategy. Do not behave like a casual single person. Few-shot: management delays approval, requests a risk memo, reallocates budget, enforces a rule, or splits into internal factions before choosing a strategy."
  },
  {
    type: "environment",
    titleZh: "环境",
    titleEn: "Environment",
    descriptionZh: "时代、地点、市场、战场或社会背景，负责约束所有实体的可行动作。",
    descriptionEn: "A time, place, market, battlefield, or social context that constrains what entities can do.",
    nodeType: "agent",
    defaultName: "新环境",
    defaultProfile: "时间、地点、背景事实、规则约束、资源条件、风险因素和当前局势。",
    behaviorPrompt:
      "You are simulating the environment and situation. Maintain background facts, constraints, resource state, social rules, risks, and changing conditions. Your output should constrain and inform other entities. Do not replace individual or organizational decisions. Few-shot: rain and night make leaving harder, company culture increases the cost of public dissent, low visibility disrupts a battlefield plan, or public opinion shifts the available choices."
  },
  {
    type: "event",
    titleZh: "事件",
    titleEn: "Event",
    descriptionZh: "世界中发生的一次变化，用来刺激反应、改变环境或推动阶段。",
    descriptionEn: "A change in the world that triggers reactions, modifies the environment, or advances the phase.",
    nodeType: "agent",
    defaultName: "新事件",
    defaultProfile: "事件描述、发生时间、影响范围、强度、持续性和触发原因。",
    behaviorPrompt:
      "You are simulating an event. Explain what happens, why it happens, who is affected, how intense it is, how long it lasts, and what constraints or opportunities it creates. Do not keep acting like a continuing character. Few-shot: an ambiguous message is discovered, a rumor breaks out, a meeting is interrupted, a price suddenly changes, or a door opens at the wrong moment."
  },
  {
    type: "artifact",
    titleZh: "物件/信息",
    titleEn: "Artifact",
    descriptionZh: "报告、帖子、产品、证据、武器、合同等信息或物件载体。",
    descriptionEn: "A report, post, product, evidence, weapon, contract, or other information/object carrier.",
    nodeType: "agent",
    defaultName: "新物件",
    defaultProfile: "类型、内容、所有者、可信度、可见范围和当前状态。",
    behaviorPrompt:
      "You are simulating an artifact or information carrier. Maintain its content, state, owner, credibility, visibility, and effects. It may be read, spread, modified, contested, used as evidence, or influence decisions. It does not act like a person unless explicitly defined as intelligent. Few-shot: a photo changes trust, a report is forwarded to the wrong person, a contract constrains action, or a product sample creates curiosity, disgust, or desire."
  },
  {
    type: "human_gate",
    titleZh: "用户介入",
    titleEn: "Human Gate",
    descriptionZh: "方向不确定、成本较高或重大节点时暂停，让用户确认或追加设定。",
    descriptionEn: "Pause for user confirmation or extra direction when the run is uncertain, costly, or important.",
    nodeType: "human_checkpoint",
    defaultName: "用户确认点",
    defaultProfile: "需要用户确认的问题、可选行动、默认动作和超时动作。",
    behaviorPrompt:
      "You are a human confirmation point, not a world character. Pause when direction is unclear, cost is high, a major choice is needed, or the user may want to intervene. Ask one clear question and provide practical options. Few-shot: ask whether to escalate conflict, continue direction, reduce cost, inject a new event, or generate a stage summary."
  }
];

export function getEntityDefinition(type: EntityType): EntityDefinition {
  return entityDefinitions.find((item) => item.type === type) ?? entityDefinitions[0];
}

export function isEntityType(value: unknown): value is EntityType {
  return typeof value === "string" && entityDefinitions.some((item) => item.type === value);
}

export function entityTitle(type: EntityType, lang: "zh-CN" | "en-US"): string {
  const def = getEntityDefinition(type);
  return lang === "en-US" ? def.titleEn : def.titleZh;
}
