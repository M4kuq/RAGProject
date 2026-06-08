import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { HelpTooltip } from "./MetricHelp";

export const CONTEXT_BUDGET_HELP = {
  description:
    "回答生成に渡す文脈をトークン上限内に収めるため、検索結果から採用する根拠と除外する根拠を記録する仕組みです。",
  direction: "大きいほど良い指標ではなく、十分な根拠を残しながら予算を超えていないかを確認します。",
  title: "Context Budget"
};

const HELP_SLOT_CLASS = "context-budget-help-slot";

export function ContextBudgetHelpPortal() {
  const [target, setTarget] = useState<HTMLElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    let observer: MutationObserver | null = null;
    let slot: HTMLElement | null = null;
    let heading: HTMLElement | null = null;

    function attach() {
      const nextHeading = Array.from(
        document.querySelectorAll<HTMLElement>(".retrieval-debug-page .admin-section h2")
      ).find((element) => element.textContent?.trim() === CONTEXT_BUDGET_HELP.title);

      const parent = nextHeading?.parentElement;
      if (!nextHeading || !parent) {
        setTarget(null);
        return false;
      }

      heading = nextHeading;
      heading.classList.add("metric-heading");
      slot = parent.querySelector<HTMLElement>(`:scope > .${HELP_SLOT_CLASS}`);
      if (!slot) {
        slot = document.createElement("span");
        slot.className = HELP_SLOT_CLASS;
        slot.style.display = "inline-flex";
        slot.style.marginLeft = "5px";
        slot.style.verticalAlign = "middle";
        heading.insertAdjacentElement("afterend", slot);
      }
      setTarget(slot);
      return true;
    }

    if (!attach()) {
      observer = new MutationObserver(() => {
        if (!cancelled) {
          attach();
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }

    return () => {
      cancelled = true;
      observer?.disconnect();
      setTarget(null);
      slot?.remove();
      heading?.classList.remove("metric-heading");
    };
  }, []);

  if (!target) {
    return null;
  }

  return createPortal(
    <HelpTooltip
      ariaLabel="Context Budget の説明"
      description={CONTEXT_BUDGET_HELP.description}
      direction={CONTEXT_BUDGET_HELP.direction}
      title={CONTEXT_BUDGET_HELP.title}
    />,
    target
  );
}
