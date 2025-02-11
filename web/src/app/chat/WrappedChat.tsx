"use client";
import { useChatContext } from "@/components/context/ChatContext";
import { ChatPage } from "./ChatPage";
import FunctionalWrapper from "../../components/chat/FunctionalWrapper";

export default function WrappedChat({
  firstMessage,
}: {
  firstMessage?: string;
}) {
  const { toggledSidebar } = useChatContext();

  return (
    <FunctionalWrapper
      initiallyToggled={toggledSidebar}
      content={(toggledSidebar, toggle) => (
        <ChatPage
          toggle={toggle}
          toggledSidebar={toggledSidebar}
          firstMessage={firstMessage}
        />
      )}
    />
  );
}
