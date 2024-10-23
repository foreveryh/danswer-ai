import {
  AuthTypeMetadata,
  getAuthTypeMetadataSS,
  getCurrentUserSS,
} from "@/lib/userSS";
import { fetchSS } from "@/lib/utilsSS";
import {
  CCPairBasicInfo,
  DocumentSet,
  Tag,
  User,
  ValidSources,
} from "@/lib/types";
import { ChatSession } from "@/app/chat/interfaces";
import { Persona } from "@/app/admin/assistants/interfaces";
import { InputPrompt } from "@/app/admin/prompt-library/interfaces";
import { FullEmbeddingModelResponse } from "@/components/embedding/interfaces";
import { Settings } from "@/app/admin/settings/interfaces";
import { fetchLLMProvidersSS } from "@/lib/llm/fetchLLMs";
import { LLMProviderDescriptor } from "@/app/admin/configuration/llm/interfaces";
import { Folder } from "@/app/chat/folders/interfaces";
import { personaComparator } from "@/app/admin/assistants/lib";
import { cookies, headers } from "next/headers";
import {
  SIDEBAR_TOGGLED_COOKIE_NAME,
  DOCUMENT_SIDEBAR_WIDTH_COOKIE_NAME,
} from "@/components/resizable/constants";
import { hasCompletedWelcomeFlowSS } from "@/components/initialSetup/welcome/WelcomeModalWrapper";
import { fetchAssistantsSS } from "../assistants/fetchAssistantsSS";
import { NEXT_PUBLIC_DEFAULT_SIDEBAR_OPEN } from "../constants";
import { checkLLMSupportsImageInput } from "../llm/utils";
import { redirect } from "next/navigation";

interface FetchChatDataResult {
  user: User | null;
  chatSessions: ChatSession[];
  ccPairs: CCPairBasicInfo[];
  availableSources: ValidSources[];
  documentSets: DocumentSet[];
  assistants: Persona[];
  tags: Tag[];
  llmProviders: LLMProviderDescriptor[];
  folders: Folder[];
  openedFolders: Record<string, boolean>;
  defaultAssistantId?: number;
  toggleSidebar: boolean;
  finalDocumentSidebarInitialWidth?: number;
  shouldShowWelcomeModal: boolean;
  userInputPrompts: InputPrompt[];
  hasAnyConnectors: boolean;
  hasImageCompatibleModel: boolean;
}

export async function fetchChatData(searchParams: {
  [key: string]: string;
}): Promise<FetchChatDataResult | { redirect: string }> {
  const tasks = [
    getAuthTypeMetadataSS(),
    getCurrentUserSS(),
    fetchSS("/manage/indexing-status"),
    fetchSS("/manage/document-set"),
    fetchAssistantsSS(),
    fetchSS("/chat/get-user-chat-sessions"),
    fetchSS("/query/valid-tags"),
    fetchLLMProvidersSS(),
    fetchSS("/folder"),
    fetchSS("/input_prompt?include_public=true"),
  ];

  let results: (
    | User
    | Response
    | AuthTypeMetadata
    | FullEmbeddingModelResponse
    | Settings
    | LLMProviderDescriptor[]
    | [Persona[], string | null]
    | null
  )[] = [null, null, null, null, null, null, null, null, null, null];
  try {
    results = await Promise.all(tasks);
  } catch (e) {
    console.log(`Some fetch failed for the main search page - ${e}`);
  }

  const authTypeMetadata = results[0] as AuthTypeMetadata | null;
  const user = results[1] as User | null;
  const ccPairsResponse = results[2] as Response | null;
  const documentSetsResponse = results[3] as Response | null;
  const [rawAssistantsList, assistantsFetchError] = results[4] as [
    Persona[],
    string | null,
  ];

  const chatSessionsResponse = results[5] as Response | null;

  const tagsResponse = results[6] as Response | null;
  const llmProviders = (results[7] || []) as LLMProviderDescriptor[];
  const foldersResponse = results[8] as Response | null;
  const userInputPromptsResponse = results[9] as Response | null;

  const authDisabled = authTypeMetadata?.authType === "disabled";
  if (!authDisabled && !user) {
    const headersList = headers();
    const fullUrl = headersList.get("x-url") || "/chat";
    const searchParamsString = new URLSearchParams(
      searchParams as unknown as Record<string, string>
    ).toString();
    const redirectUrl = searchParamsString
      ? `${fullUrl}?${searchParamsString}`
      : fullUrl;
    return redirect(`/auth/login?next=${encodeURIComponent(redirectUrl)}`);
  }

  if (user && !user.is_verified && authTypeMetadata?.requiresVerification) {
    return { redirect: "/auth/waiting-on-verification" };
  }

  let ccPairs: CCPairBasicInfo[] = [];
  if (ccPairsResponse?.ok) {
    ccPairs = await ccPairsResponse.json();
  } else {
    console.log(`Failed to fetch connectors - ${ccPairsResponse?.status}`);
  }
  const availableSources: ValidSources[] = [];
  ccPairs.forEach((ccPair) => {
    if (!availableSources.includes(ccPair.source)) {
      availableSources.push(ccPair.source);
    }
  });

  let chatSessions: ChatSession[] = [];
  if (chatSessionsResponse?.ok) {
    chatSessions = (await chatSessionsResponse.json()).sessions;
  } else {
    console.log(
      `Failed to fetch chat sessions - ${chatSessionsResponse?.text()}`
    );
  }

  chatSessions.sort(
    (a, b) =>
      new Date(b.time_created).getTime() - new Date(a.time_created).getTime()
  );

  let documentSets: DocumentSet[] = [];
  if (documentSetsResponse?.ok) {
    documentSets = await documentSetsResponse.json();
  } else {
    console.log(
      `Failed to fetch document sets - ${documentSetsResponse?.status}`
    );
  }

  let userInputPrompts: InputPrompt[] = [];
  if (userInputPromptsResponse?.ok) {
    userInputPrompts = await userInputPromptsResponse.json();
  } else {
    console.log(
      `Failed to fetch user input prompts - ${userInputPromptsResponse?.status}`
    );
  }

  let assistants = rawAssistantsList;
  if (assistantsFetchError) {
    console.log(`Failed to fetch assistants - ${assistantsFetchError}`);
  }
  // remove those marked as hidden by an admin

  assistants = assistants.filter((assistant) => assistant.is_visible);

  // sort them in priority order
  assistants.sort(personaComparator);

  let tags: Tag[] = [];
  if (tagsResponse?.ok) {
    tags = (await tagsResponse.json()).tags;
  } else {
    console.log(`Failed to fetch tags - ${tagsResponse?.status}`);
  }

  const defaultAssistantIdRaw = searchParams["assistantId"];
  const defaultAssistantId = defaultAssistantIdRaw
    ? parseInt(defaultAssistantIdRaw)
    : undefined;

  const documentSidebarCookieInitialWidth = cookies().get(
    DOCUMENT_SIDEBAR_WIDTH_COOKIE_NAME
  );
  const sidebarToggled = cookies().get(SIDEBAR_TOGGLED_COOKIE_NAME);

  const toggleSidebar = sidebarToggled
    ? sidebarToggled.value.toLocaleLowerCase() == "true" || false
    : NEXT_PUBLIC_DEFAULT_SIDEBAR_OPEN;

  const finalDocumentSidebarInitialWidth = documentSidebarCookieInitialWidth
    ? parseInt(documentSidebarCookieInitialWidth.value)
    : undefined;

  const hasAnyConnectors = ccPairs.length > 0;
  const shouldShowWelcomeModal =
    !llmProviders.length &&
    !hasCompletedWelcomeFlowSS() &&
    !hasAnyConnectors &&
    (!user || user.role === "admin");

  // if no connectors are setup, only show personas that are pure
  // passthrough and don't do any retrieval
  if (!hasAnyConnectors) {
    assistants = assistants.filter((assistant) => assistant.num_chunks === 0);
  }

  const hasImageCompatibleModel = llmProviders.some(
    (provider) =>
      provider.provider === "openai" ||
      provider.model_names.some((model) => checkLLMSupportsImageInput(model))
  );

  if (!hasImageCompatibleModel) {
    assistants = assistants.filter(
      (assistant) =>
        !assistant.tools.some(
          (tool) => tool.in_code_tool_id === "ImageGenerationTool"
        )
    );
  }

  let folders: Folder[] = [];
  if (foldersResponse?.ok) {
    folders = (await foldersResponse.json()).folders as Folder[];
  } else {
    console.log(`Failed to fetch folders - ${foldersResponse?.status}`);
  }

  const openedFoldersCookie = cookies().get("openedFolders");
  const openedFolders = openedFoldersCookie
    ? JSON.parse(openedFoldersCookie.value)
    : {};

  return {
    user,
    chatSessions,
    ccPairs,
    availableSources,
    documentSets,
    assistants,
    tags,
    llmProviders,
    folders,
    openedFolders,
    defaultAssistantId,
    finalDocumentSidebarInitialWidth,
    toggleSidebar,
    shouldShowWelcomeModal,
    userInputPrompts,
    hasAnyConnectors,
    hasImageCompatibleModel,
  };
}
