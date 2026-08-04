import { brandBlockPlugin } from "./brand-block.mjs";
import { bodyPanelPlugin } from "./body-panel.mjs";
import { conversationPanelPlugin } from "./conversation-panel.mjs";
import { executePanelPlugin } from "./execute-panel.mjs";
import { historyBlockPlugin } from "./history-block.mjs";
import { inspectorPanelPlugin } from "./inspector-panel.mjs";
import { knowledgePanelPlugin } from "./knowledge-panel.mjs";
import { lifePanelPlugin } from "./life-panel.mjs";
import { lifeSummaryBlockPlugin } from "./life-summary-block.mjs";
import { navRailPlugin } from "./nav-rail.mjs";
import { personaSideBlockPlugin } from "./persona-side-block.mjs";
import { runtimeStatusBlockPlugin } from "./runtime-status-block.mjs";
import { settingsPanelPlugin } from "./settings-panel.mjs";
import { skillsPanelPlugin } from "./skills-panel.mjs";
import { skillsSideBlockPlugin } from "./skills-side-block.mjs";
import { vrmInspectorPanelPlugin } from "./vrm-inspector-panel.mjs";
import { avatarPanelPlugin } from "./avatar-panel.mjs";

export const plugins = [
  navRailPlugin,
  brandBlockPlugin,
  lifeSummaryBlockPlugin,
  personaSideBlockPlugin,
  skillsSideBlockPlugin,
  runtimeStatusBlockPlugin,
  historyBlockPlugin,
  conversationPanelPlugin,
  executePanelPlugin,
  knowledgePanelPlugin,
  skillsPanelPlugin,
  bodyPanelPlugin,
  lifePanelPlugin,
  settingsPanelPlugin,
  inspectorPanelPlugin,
  vrmInspectorPanelPlugin,
  avatarPanelPlugin,
];
