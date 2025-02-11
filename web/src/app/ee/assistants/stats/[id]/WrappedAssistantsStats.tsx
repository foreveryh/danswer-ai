"use client";
import SidebarWrapper from "../../../../assistants/SidebarWrapper";
import { AssistantStats } from "./AssistantStats";

export default function WrappedAssistantsStats({
  initiallyToggled,
  assistantId,
}: {
  initiallyToggled: boolean;
  assistantId: number;
}) {
  return (
    <SidebarWrapper initiallyToggled={initiallyToggled}>
      <AssistantStats assistantId={assistantId} />
    </SidebarWrapper>
  );
}
