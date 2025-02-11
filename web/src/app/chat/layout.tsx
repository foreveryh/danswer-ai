import { redirect } from "next/navigation";
import { unstable_noStore as noStore } from "next/cache";
import { fetchChatData } from "@/lib/chat/fetchChatData";
import { ChatProvider } from "@/components/context/ChatContext";
import { InstantSSRAutoRefresh } from "@/components/SSRAutoRefresh";

export default async function Layout({
  children,
}: {
  children: React.ReactNode;
}) {
  noStore();

  // Ensure searchParams is an object, even if it's empty
  const safeSearchParams = {};

  const data = await fetchChatData(
    safeSearchParams as { [key: string]: string }
  );

  if ("redirect" in data) {
    redirect(data.redirect);
  }

  const {
    chatSessions,
    availableSources,
    user,
    documentSets,
    tags,
    llmProviders,
    folders,
    openedFolders,
    toggleSidebar,
    defaultAssistantId,
    shouldShowWelcomeModal,
    ccPairs,
    inputPrompts,
    proSearchToggled,
  } = data;

  return (
    <>
      <InstantSSRAutoRefresh />
      <ChatProvider
        value={{
          proSearchToggled,
          inputPrompts,
          chatSessions,
          toggledSidebar: toggleSidebar,
          availableSources,
          ccPairs,
          documentSets,
          tags,
          availableDocumentSets: documentSets,
          availableTags: tags,
          llmProviders,
          folders,
          openedFolders,
          shouldShowWelcomeModal,
          defaultAssistantId,
        }}
      >
        {children}
      </ChatProvider>
    </>
  );
}
