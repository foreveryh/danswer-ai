"use client";

import { redirect, useRouter, useSearchParams } from "next/navigation";
import {
  BackendChatSession,
  BackendMessage,
  BUFFER_COUNT,
  ChatFileType,
  ChatSession,
  ChatSessionSharedStatus,
  FileDescriptor,
  FileChatDisplay,
  Message,
  MessageResponseIDInfo,
  RetrievalType,
  StreamingError,
  ToolCallMetadata,
  SubQuestionDetail,
  constructSubQuestions,
  DocumentsResponse,
} from "./interfaces";

import Prism from "prismjs";
import Cookies from "js-cookie";
import { HistorySidebar } from "./sessionSidebar/HistorySidebar";
import { Persona } from "../admin/assistants/interfaces";
import { HealthCheckBanner } from "@/components/health/healthcheck";
import {
  buildChatUrl,
  buildLatestMessageChain,
  createChatSession,
  deleteAllChatSessions,
  getCitedDocumentsFromMessage,
  getHumanAndAIMessageFromMessageNumber,
  getLastSuccessfulMessageId,
  handleChatFeedback,
  nameChatSession,
  PacketType,
  personaIncludesRetrieval,
  processRawChatHistory,
  removeMessage,
  sendMessage,
  setMessageAsLatest,
  updateParentChildren,
  uploadFilesForChat,
  useScrollonStream,
} from "./lib";
import {
  Dispatch,
  SetStateAction,
  use,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePopup } from "@/components/admin/connectors/Popup";
import { SEARCH_PARAM_NAMES, shouldSubmitOnLoad } from "./searchParams";
import { useDocumentSelection } from "./useDocumentSelection";
import { LlmOverride, useFilters, useLlmOverride } from "@/lib/hooks";
import { ChatState, FeedbackType, RegenerationState } from "./types";
import { DocumentResults } from "./documentSidebar/DocumentResults";
import { OnyxInitializingLoader } from "@/components/OnyxInitializingLoader";
import { FeedbackModal } from "./modal/FeedbackModal";
import { ShareChatSessionModal } from "./modal/ShareChatSessionModal";
import { FiArrowDown } from "react-icons/fi";
import { ChatIntro } from "./ChatIntro";
import { AIMessage, HumanMessage } from "./message/Messages";
import { StarterMessages } from "../../components/assistants/StarterMessage";
import {
  AnswerPiecePacket,
  OnyxDocument,
  DocumentInfoPacket,
  StreamStopInfo,
  StreamStopReason,
  SubQueryPiece,
  SubQuestionPiece,
  AgentAnswerPiece,
  RefinedAnswerImprovement,
} from "@/lib/search/interfaces";
import { buildFilters } from "@/lib/search/utils";
import { SettingsContext } from "@/components/settings/SettingsProvider";
import Dropzone from "react-dropzone";
import { checkLLMSupportsImageInput, getFinalLLM } from "@/lib/llm/utils";
import { ChatInputBar } from "./input/ChatInputBar";
import { useChatContext } from "@/components/context/ChatContext";
import { v4 as uuidv4 } from "uuid";
import { ChatPopup } from "./ChatPopup";

import FunctionalHeader from "@/components/chat/Header";
import { useSidebarVisibility } from "@/components/chat/hooks";
import {
  PRO_SEARCH_TOGGLED_COOKIE_NAME,
  SIDEBAR_TOGGLED_COOKIE_NAME,
} from "@/components/resizable/constants";
import FixedLogo from "../../components/logo/FixedLogo";

import { DeleteEntityModal } from "../../components/modals/DeleteEntityModal";
import { MinimalMarkdown } from "@/components/chat/MinimalMarkdown";
import ExceptionTraceModal from "@/components/modals/ExceptionTraceModal";

import {
  INTERNET_SEARCH_TOOL_ID,
  SEARCH_TOOL_ID,
  SEARCH_TOOL_NAME,
} from "./tools/constants";
import { useUser } from "@/components/user/UserProvider";
import { ApiKeyModal } from "@/components/llm/ApiKeyModal";
import BlurBackground from "../../components/chat/BlurBackground";
import { NoAssistantModal } from "@/components/modals/NoAssistantModal";
import { useAssistants } from "@/components/context/AssistantsContext";
import TextView from "@/components/chat/TextView";
import { Modal } from "@/components/Modal";
import { useSendMessageToParent } from "@/lib/extension/utils";
import {
  CHROME_MESSAGE,
  SUBMIT_MESSAGE_TYPES,
} from "@/lib/extension/constants";

import { getSourceMetadata } from "@/lib/sources";
import { UserSettingsModal } from "./modal/UserSettingsModal";
import { AlignStartVertical } from "lucide-react";
import { AgenticMessage } from "./message/AgenticMessage";
import AssistantModal from "../assistants/mine/AssistantModal";
import {
  OperatingSystem,
  useOperatingSystem,
  useSidebarShortcut,
} from "@/lib/browserUtilities";
import { Button } from "@/components/ui/button";

const TEMP_USER_MESSAGE_ID = -1;
const TEMP_ASSISTANT_MESSAGE_ID = -2;
const SYSTEM_MESSAGE_ID = -3;

export function ChatPage({
  toggle,
  documentSidebarInitialWidth,
  toggledSidebar,
  firstMessage,
}: {
  toggle: (toggled?: boolean) => void;
  documentSidebarInitialWidth?: number;
  toggledSidebar: boolean;
  firstMessage?: string;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const {
    chatSessions,
    ccPairs,
    tags,
    documentSets,
    llmProviders,
    folders,
    shouldShowWelcomeModal,
    refreshChatSessions,
    proSearchToggled,
  } = useChatContext();

  const defaultAssistantIdRaw = searchParams.get(SEARCH_PARAM_NAMES.PERSONA_ID);
  const defaultAssistantId = defaultAssistantIdRaw
    ? parseInt(defaultAssistantIdRaw)
    : undefined;

  function useScreenSize() {
    const [screenSize, setScreenSize] = useState({
      width: typeof window !== "undefined" ? window.innerWidth : 0,
      height: typeof window !== "undefined" ? window.innerHeight : 0,
    });

    useEffect(() => {
      const handleResize = () => {
        setScreenSize({
          width: window.innerWidth,
          height: window.innerHeight,
        });
      };

      window.addEventListener("resize", handleResize);
      return () => window.removeEventListener("resize", handleResize);
    }, []);

    return screenSize;
  }

  const { height: screenHeight } = useScreenSize();

  const getContainerHeight = () => {
    if (autoScrollEnabled) return undefined;

    if (screenHeight < 600) return "20vh";
    if (screenHeight < 1200) return "30vh";
    return "40vh";
  };

  // handle redirect if chat page is disabled
  // NOTE: this must be done here, in a client component since
  // settings are passed in via Context and therefore aren't
  // available in server-side components
  const settings = useContext(SettingsContext);
  const enterpriseSettings = settings?.enterpriseSettings;

  const [documentSidebarToggled, setDocumentSidebarToggled] = useState(false);
  const [proSearchEnabled, setProSearchEnabled] = useState(proSearchToggled);
  const [streamingAllowed, setStreamingAllowed] = useState(false);
  const toggleProSearch = () => {
    Cookies.set(
      PRO_SEARCH_TOGGLED_COOKIE_NAME,
      String(!proSearchEnabled).toLocaleLowerCase()
    );
    setProSearchEnabled(!proSearchEnabled);
  };

  const [userSettingsToggled, setUserSettingsToggled] = useState(false);

  const {
    assistants: availableAssistants,
    finalAssistants,
    pinnedAssistants,
  } = useAssistants();

  const [showApiKeyModal, setShowApiKeyModal] = useState(
    !shouldShowWelcomeModal
  );

  const { user, isAdmin } = useUser();
  const slackChatId = searchParams.get("slackChatId");
  const existingChatIdRaw = searchParams.get("chatId");

  const [showHistorySidebar, setShowHistorySidebar] = useState(false); // State to track if sidebar is open

  const existingChatSessionId = existingChatIdRaw ? existingChatIdRaw : null;

  const selectedChatSession = chatSessions.find(
    (chatSession) => chatSession.id === existingChatSessionId
  );

  useEffect(() => {
    if (user?.is_anonymous_user) {
      Cookies.set(
        SIDEBAR_TOGGLED_COOKIE_NAME,
        String(!toggledSidebar).toLocaleLowerCase()
      );
      toggle(false);
    }
  }, [user]);

  const processSearchParamsAndSubmitMessage = (searchParamsString: string) => {
    const newSearchParams = new URLSearchParams(searchParamsString);
    const message = newSearchParams.get("user-prompt");

    filterManager.buildFiltersFromQueryString(
      newSearchParams.toString(),
      availableSources,
      documentSets.map((ds) => ds.name),
      tags
    );

    const fileDescriptorString = newSearchParams.get(SEARCH_PARAM_NAMES.FILES);
    const overrideFileDescriptors: FileDescriptor[] = fileDescriptorString
      ? JSON.parse(decodeURIComponent(fileDescriptorString))
      : [];

    newSearchParams.delete(SEARCH_PARAM_NAMES.SEND_ON_LOAD);

    router.replace(`?${newSearchParams.toString()}`, { scroll: false });

    // If there's a message, submit it
    if (message) {
      setSubmittedMessage(message);
      onSubmit({ messageOverride: message, overrideFileDescriptors });
    }
  };

  const chatSessionIdRef = useRef<string | null>(existingChatSessionId);

  // Only updates on session load (ie. rename / switching chat session)
  // Useful for determining which session has been loaded (i.e. still on `new, empty session` or `previous session`)
  const loadedIdSessionRef = useRef<string | null>(existingChatSessionId);

  const existingChatSessionAssistantId = selectedChatSession?.persona_id;
  const [selectedAssistant, setSelectedAssistant] = useState<
    Persona | undefined
  >(
    // NOTE: look through available assistants here, so that even if the user
    // has hidden this assistant it still shows the correct assistant when
    // going back to an old chat session
    existingChatSessionAssistantId !== undefined
      ? availableAssistants.find(
          (assistant) => assistant.id === existingChatSessionAssistantId
        )
      : defaultAssistantId !== undefined
        ? availableAssistants.find(
            (assistant) => assistant.id === defaultAssistantId
          )
        : undefined
  );
  // Gather default temperature settings
  const search_param_temperature = searchParams.get(
    SEARCH_PARAM_NAMES.TEMPERATURE
  );

  const defaultTemperature = search_param_temperature
    ? parseFloat(search_param_temperature)
    : selectedAssistant?.tools.some(
          (tool) =>
            tool.in_code_tool_id === SEARCH_TOOL_ID ||
            tool.in_code_tool_id === INTERNET_SEARCH_TOOL_ID
        )
      ? 0
      : 0.7;

  const setSelectedAssistantFromId = (assistantId: number) => {
    // NOTE: also intentionally look through available assistants here, so that
    // even if the user has hidden an assistant they can still go back to it
    // for old chats
    setSelectedAssistant(
      availableAssistants.find((assistant) => assistant.id === assistantId)
    );
  };

  const [alternativeAssistant, setAlternativeAssistant] =
    useState<Persona | null>(null);

  const [presentingDocument, setPresentingDocument] =
    useState<OnyxDocument | null>(null);

  // Current assistant is decided based on this ordering
  // 1. Alternative assistant (assistant selected explicitly by user)
  // 2. Selected assistant (assistnat default in this chat session)
  // 3. First pinned assistants (ordered list of pinned assistants)
  // 4. Available assistants (ordered list of available assistants)
  // Relevant test: `live_assistant.spec.ts`
  const liveAssistant: Persona | undefined = useMemo(
    () =>
      alternativeAssistant ||
      selectedAssistant ||
      pinnedAssistants[0] ||
      availableAssistants[0],
    [
      alternativeAssistant,
      selectedAssistant,
      pinnedAssistants,
      availableAssistants,
    ]
  );

  const llmOverrideManager = useLlmOverride(
    llmProviders,
    selectedChatSession,
    liveAssistant
  );

  const noAssistants = liveAssistant == null || liveAssistant == undefined;

  const availableSources = ccPairs.map((ccPair) => ccPair.source);
  const uniqueSources = Array.from(new Set(availableSources));
  const sources = uniqueSources.map((source) => getSourceMetadata(source));

  const stopGenerating = () => {
    const currentSession = currentSessionId();
    const controller = abortControllers.get(currentSession);
    if (controller) {
      controller.abort();
      setAbortControllers((prev) => {
        const newControllers = new Map(prev);
        newControllers.delete(currentSession);
        return newControllers;
      });
    }

    const lastMessage = messageHistory[messageHistory.length - 1];
    if (
      lastMessage &&
      lastMessage.type === "assistant" &&
      lastMessage.toolCall &&
      lastMessage.toolCall.tool_result === undefined
    ) {
      const newCompleteMessageMap = new Map(
        currentMessageMap(completeMessageDetail)
      );
      const updatedMessage = { ...lastMessage, toolCall: null };
      newCompleteMessageMap.set(lastMessage.messageId, updatedMessage);
      updateCompleteMessageDetail(currentSession, newCompleteMessageMap);
    }

    updateChatState("input", currentSession);
  };

  // this is for "@"ing assistants

  // this is used to track which assistant is being used to generate the current message
  // for example, this would come into play when:
  // 1. default assistant is `Onyx`
  // 2. we "@"ed the `GPT` assistant and sent a message
  // 3. while the `GPT` assistant message is generating, we "@" the `Paraphrase` assistant
  const [alternativeGeneratingAssistant, setAlternativeGeneratingAssistant] =
    useState<Persona | null>(null);

  // used to track whether or not the initial "submit on load" has been performed
  // this only applies if `?submit-on-load=true` or `?submit-on-load=1` is in the URL
  // NOTE: this is required due to React strict mode, where all `useEffect` hooks
  // are run twice on initial load during development
  const submitOnLoadPerformed = useRef<boolean>(false);

  const { popup, setPopup } = usePopup();

  // fetch messages for the chat session
  const [isFetchingChatMessages, setIsFetchingChatMessages] = useState(
    existingChatSessionId !== null
  );

  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    Prism.highlightAll();
    setIsReady(true);
  }, []);

  useEffect(() => {
    const priorChatSessionId = chatSessionIdRef.current;
    const loadedSessionId = loadedIdSessionRef.current;
    chatSessionIdRef.current = existingChatSessionId;
    loadedIdSessionRef.current = existingChatSessionId;

    textAreaRef.current?.focus();

    // only clear things if we're going from one chat session to another
    const isChatSessionSwitch = existingChatSessionId !== priorChatSessionId;
    if (isChatSessionSwitch) {
      // de-select documents
      clearSelectedDocuments();

      // reset all filters
      filterManager.setSelectedDocumentSets([]);
      filterManager.setSelectedSources([]);
      filterManager.setSelectedTags([]);
      filterManager.setTimeRange(null);

      // remove uploaded files
      setCurrentMessageFiles([]);

      // if switching from one chat to another, then need to scroll again
      // if we're creating a brand new chat, then don't need to scroll
      if (chatSessionIdRef.current !== null) {
        setHasPerformedInitialScroll(false);
      }
    }

    async function initialSessionFetch() {
      if (existingChatSessionId === null) {
        setIsFetchingChatMessages(false);
        if (defaultAssistantId !== undefined) {
          setSelectedAssistantFromId(defaultAssistantId);
        } else {
          setSelectedAssistant(undefined);
        }
        updateCompleteMessageDetail(null, new Map());
        setChatSessionSharedStatus(ChatSessionSharedStatus.Private);

        // if we're supposed to submit on initial load, then do that here
        if (
          shouldSubmitOnLoad(searchParams) &&
          !submitOnLoadPerformed.current
        ) {
          submitOnLoadPerformed.current = true;
          await onSubmit();
        }
        return;
      }

      clearSelectedDocuments();
      setIsFetchingChatMessages(true);
      const response = await fetch(
        `/api/chat/get-chat-session/${existingChatSessionId}`
      );

      const session = await response.json();
      const chatSession = session as BackendChatSession;
      setSelectedAssistantFromId(chatSession.persona_id);

      const newMessageMap = processRawChatHistory(chatSession.messages);
      const newMessageHistory = buildLatestMessageChain(newMessageMap);

      // Update message history except for edge where where
      // last message is an error and we're on a new chat.
      // This corresponds to a "renaming" of chat, which occurs after first message
      // stream
      if (
        (messageHistory[messageHistory.length - 1]?.type !== "error" ||
          loadedSessionId != null) &&
        !currentChatAnswering()
      ) {
        const latestMessageId =
          newMessageHistory[newMessageHistory.length - 1]?.messageId;

        setSelectedMessageForDocDisplay(
          latestMessageId !== undefined ? latestMessageId : null
        );

        updateCompleteMessageDetail(chatSession.chat_session_id, newMessageMap);
      }

      setChatSessionSharedStatus(chatSession.shared_status);

      // go to bottom. If initial load, then do a scroll,
      // otherwise just appear at the bottom

      scrollInitialized.current = false;

      if (!hasPerformedInitialScroll) {
        clientScrollToBottom();
      } else if (isChatSessionSwitch) {
        clientScrollToBottom(true);
      }

      setIsFetchingChatMessages(false);

      // if this is a seeded chat, then kick off the AI message generation
      if (
        newMessageHistory.length === 1 &&
        !submitOnLoadPerformed.current &&
        searchParams.get(SEARCH_PARAM_NAMES.SEEDED) === "true"
      ) {
        submitOnLoadPerformed.current = true;
        const seededMessage = newMessageHistory[0].message;
        await onSubmit({
          isSeededChat: true,
          messageOverride: seededMessage,
        });
        // force re-name if the chat session doesn't have one
        if (!chatSession.description) {
          await nameChatSession(existingChatSessionId);
          refreshChatSessions();
        }
      } else if (newMessageHistory.length === 2 && !chatSession.description) {
        await nameChatSession(existingChatSessionId);
        refreshChatSessions();
      }
    }

    initialSessionFetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existingChatSessionId, searchParams.get(SEARCH_PARAM_NAMES.PERSONA_ID)]);

  const [message, setMessage] = useState(
    searchParams.get(SEARCH_PARAM_NAMES.USER_PROMPT) || ""
  );

  const [completeMessageDetail, setCompleteMessageDetail] = useState<
    Map<string | null, Map<number, Message>>
  >(new Map());

  const updateCompleteMessageDetail = (
    sessionId: string | null,
    messageMap: Map<number, Message>
  ) => {
    setCompleteMessageDetail((prevState) => {
      const newState = new Map(prevState);
      newState.set(sessionId, messageMap);
      return newState;
    });
  };

  const currentMessageMap = (
    messageDetail: Map<string | null, Map<number, Message>>
  ) => {
    return (
      messageDetail.get(chatSessionIdRef.current) || new Map<number, Message>()
    );
  };
  const currentSessionId = (): string => {
    return chatSessionIdRef.current!;
  };

  const upsertToCompleteMessageMap = ({
    messages,
    completeMessageMapOverride,
    chatSessionId,
    replacementsMap = null,
    makeLatestChildMessage = false,
  }: {
    messages: Message[];
    // if calling this function repeatedly with short delay, stay may not update in time
    // and result in weird behavipr
    completeMessageMapOverride?: Map<number, Message> | null;
    chatSessionId?: string;
    replacementsMap?: Map<number, number> | null;
    makeLatestChildMessage?: boolean;
  }) => {
    // deep copy
    const frozenCompleteMessageMap =
      completeMessageMapOverride || currentMessageMap(completeMessageDetail);
    const newCompleteMessageMap = structuredClone(frozenCompleteMessageMap);

    if (newCompleteMessageMap.size === 0) {
      const systemMessageId = messages[0].parentMessageId || SYSTEM_MESSAGE_ID;
      const firstMessageId = messages[0].messageId;
      const dummySystemMessage: Message = {
        messageId: systemMessageId,
        message: "",
        type: "system",
        files: [],
        toolCall: null,
        parentMessageId: null,
        childrenMessageIds: [firstMessageId],
        latestChildMessageId: firstMessageId,
      };
      newCompleteMessageMap.set(
        dummySystemMessage.messageId,
        dummySystemMessage
      );
      messages[0].parentMessageId = systemMessageId;
    }

    messages.forEach((message) => {
      const idToReplace = replacementsMap?.get(message.messageId);
      if (idToReplace) {
        removeMessage(idToReplace, newCompleteMessageMap);
      }

      // update childrenMessageIds for the parent
      if (
        !newCompleteMessageMap.has(message.messageId) &&
        message.parentMessageId !== null
      ) {
        updateParentChildren(message, newCompleteMessageMap, true);
      }
      newCompleteMessageMap.set(message.messageId, message);
    });
    // if specified, make these new message the latest of the current message chain
    if (makeLatestChildMessage) {
      const currentMessageChain = buildLatestMessageChain(
        frozenCompleteMessageMap
      );
      const latestMessage = currentMessageChain[currentMessageChain.length - 1];
      if (latestMessage) {
        newCompleteMessageMap.get(
          latestMessage.messageId
        )!.latestChildMessageId = messages[0].messageId;
      }
    }

    const newCompleteMessageDetail = {
      sessionId: chatSessionId || currentSessionId(),
      messageMap: newCompleteMessageMap,
    };

    updateCompleteMessageDetail(
      chatSessionId || currentSessionId(),
      newCompleteMessageMap
    );
    return newCompleteMessageDetail;
  };

  const messageHistory = buildLatestMessageChain(
    currentMessageMap(completeMessageDetail)
  );

  const [submittedMessage, setSubmittedMessage] = useState(firstMessage || "");

  const [chatState, setChatState] = useState<Map<string | null, ChatState>>(
    new Map([[chatSessionIdRef.current, firstMessage ? "loading" : "input"]])
  );

  const [regenerationState, setRegenerationState] = useState<
    Map<string | null, RegenerationState | null>
  >(new Map([[null, null]]));

  const [abortControllers, setAbortControllers] = useState<
    Map<string | null, AbortController>
  >(new Map());

  // Updates "null" session values to new session id for
  // regeneration, chat, and abort controller state, messagehistory
  const updateStatesWithNewSessionId = (newSessionId: string) => {
    const updateState = (
      setState: Dispatch<SetStateAction<Map<string | null, any>>>,
      defaultValue?: any
    ) => {
      setState((prevState) => {
        const newState = new Map(prevState);
        const existingState = newState.get(null);
        if (existingState !== undefined) {
          newState.set(newSessionId, existingState);
          newState.delete(null);
        } else if (defaultValue !== undefined) {
          newState.set(newSessionId, defaultValue);
        }
        return newState;
      });
    };

    updateState(setRegenerationState);
    updateState(setChatState);
    updateState(setAbortControllers);

    // Update completeMessageDetail
    setCompleteMessageDetail((prevState) => {
      const newState = new Map(prevState);
      const existingMessages = newState.get(null);
      if (existingMessages) {
        newState.set(newSessionId, existingMessages);
        newState.delete(null);
      }
      return newState;
    });

    // Update chatSessionIdRef
    chatSessionIdRef.current = newSessionId;
  };

  const updateChatState = (newState: ChatState, sessionId?: string | null) => {
    setChatState((prevState) => {
      const newChatState = new Map(prevState);
      newChatState.set(
        sessionId !== undefined ? sessionId : currentSessionId(),
        newState
      );
      return newChatState;
    });
  };

  const currentChatState = (): ChatState => {
    return chatState.get(currentSessionId()) || "input";
  };

  const currentChatAnswering = () => {
    return (
      currentChatState() == "toolBuilding" ||
      currentChatState() == "streaming" ||
      currentChatState() == "loading"
    );
  };

  const updateRegenerationState = (
    newState: RegenerationState | null,
    sessionId?: string | null
  ) => {
    const newRegenerationState = new Map(regenerationState);
    newRegenerationState.set(
      sessionId !== undefined && sessionId != null
        ? sessionId
        : currentSessionId(),
      newState
    );

    setRegenerationState((prevState) => {
      const newRegenerationState = new Map(prevState);
      newRegenerationState.set(
        sessionId !== undefined && sessionId != null
          ? sessionId
          : currentSessionId(),
        newState
      );
      return newRegenerationState;
    });
  };

  const resetRegenerationState = (sessionId?: string | null) => {
    updateRegenerationState(null, sessionId);
  };

  const currentRegenerationState = (): RegenerationState | null => {
    return regenerationState.get(currentSessionId()) || null;
  };

  const [canContinue, setCanContinue] = useState<Map<string | null, boolean>>(
    new Map([[null, false]])
  );

  const updateCanContinue = (newState: boolean, sessionId?: string | null) => {
    setCanContinue((prevState) => {
      const newCanContinueState = new Map(prevState);
      newCanContinueState.set(
        sessionId !== undefined ? sessionId : currentSessionId(),
        newState
      );
      return newCanContinueState;
    });
  };

  const currentCanContinue = (): boolean => {
    return canContinue.get(currentSessionId()) || false;
  };

  const currentSessionChatState = currentChatState();
  const currentSessionRegenerationState = currentRegenerationState();

  // uploaded files
  const [currentMessageFiles, setCurrentMessageFiles] = useState<
    FileDescriptor[]
  >([]);

  // for document display
  // NOTE: -1 is a special designation that means the latest AI message
  const [selectedMessageForDocDisplay, setSelectedMessageForDocDisplay] =
    useState<number | null>(null);

  const { aiMessage } = selectedMessageForDocDisplay
    ? getHumanAndAIMessageFromMessageNumber(
        messageHistory,
        selectedMessageForDocDisplay
      )
    : { aiMessage: null };

  const [chatSessionSharedStatus, setChatSessionSharedStatus] =
    useState<ChatSessionSharedStatus>(ChatSessionSharedStatus.Private);

  useEffect(() => {
    if (messageHistory.length === 0 && chatSessionIdRef.current === null) {
      // Select from available assistants so shared assistants appear.
      setSelectedAssistant(
        availableAssistants.find((persona) => persona.id === defaultAssistantId)
      );
    }
  }, [defaultAssistantId, availableAssistants, messageHistory.length]);

  useEffect(() => {
    if (
      submittedMessage &&
      currentSessionChatState === "loading" &&
      messageHistory.length == 0
    ) {
      window.parent.postMessage(
        { type: CHROME_MESSAGE.LOAD_NEW_CHAT_PAGE },
        "*"
      );
    }
  }, [submittedMessage, currentSessionChatState]);

  const [
    selectedDocuments,
    toggleDocumentSelection,
    clearSelectedDocuments,
    selectedDocumentTokens,
  ] = useDocumentSelection();
  // just choose a conservative default, this will be updated in the
  // background on initial load / on persona change
  const [maxTokens, setMaxTokens] = useState<number>(4096);

  // fetch # of allowed document tokens for the selected Persona
  useEffect(() => {
    async function fetchMaxTokens() {
      const response = await fetch(
        `/api/chat/max-selected-document-tokens?persona_id=${liveAssistant?.id}`
      );
      if (response.ok) {
        const maxTokens = (await response.json()).max_tokens as number;
        setMaxTokens(maxTokens);
      }
    }
    fetchMaxTokens();
  }, [liveAssistant]);

  const filterManager = useFilters();

  const [currentFeedback, setCurrentFeedback] = useState<
    [FeedbackType, number] | null
  >(null);

  const [sharingModalVisible, setSharingModalVisible] =
    useState<boolean>(false);

  const [aboveHorizon, setAboveHorizon] = useState(false);

  const scrollableDivRef = useRef<HTMLDivElement>(null);
  const lastMessageRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLDivElement>(null);
  const endDivRef = useRef<HTMLDivElement>(null);
  const endPaddingRef = useRef<HTMLDivElement>(null);

  const previousHeight = useRef<number>(
    inputRef.current?.getBoundingClientRect().height!
  );
  const scrollDist = useRef<number>(0);

  const updateScrollTracking = () => {
    const scrollDistance =
      endDivRef?.current?.getBoundingClientRect()?.top! -
      inputRef?.current?.getBoundingClientRect()?.top!;
    scrollDist.current = scrollDistance;
    setAboveHorizon(scrollDist.current > 500);
  };

  useEffect(() => {
    const scrollableDiv = scrollableDivRef.current;
    if (scrollableDiv) {
      scrollableDiv.addEventListener("scroll", updateScrollTracking);
      return () => {
        scrollableDiv.removeEventListener("scroll", updateScrollTracking);
      };
    }
  }, []);

  const handleInputResize = () => {
    setTimeout(() => {
      if (
        inputRef.current &&
        lastMessageRef.current &&
        !waitForScrollRef.current
      ) {
        const newHeight: number =
          inputRef.current?.getBoundingClientRect().height!;
        const heightDifference = newHeight - previousHeight.current;
        if (
          previousHeight.current &&
          heightDifference != 0 &&
          endPaddingRef.current &&
          scrollableDivRef &&
          scrollableDivRef.current
        ) {
          endPaddingRef.current.style.transition = "height 0.3s ease-out";
          endPaddingRef.current.style.height = `${Math.max(
            newHeight - 50,
            0
          )}px`;

          if (autoScrollEnabled) {
            scrollableDivRef?.current.scrollBy({
              left: 0,
              top: Math.max(heightDifference, 0),
              behavior: "smooth",
            });
          }
        }
        previousHeight.current = newHeight;
      }
    }, 100);
  };

  const clientScrollToBottom = (fast?: boolean) => {
    waitForScrollRef.current = true;

    setTimeout(() => {
      if (!endDivRef.current || !scrollableDivRef.current) {
        console.error("endDivRef or scrollableDivRef not found");
        return;
      }

      const rect = endDivRef.current.getBoundingClientRect();
      const isVisible = rect.top >= 0 && rect.bottom <= window.innerHeight;

      if (isVisible) return;

      // Check if all messages are currently rendered
      if (currentVisibleRange.end < messageHistory.length) {
        // Update visible range to include the last messages
        updateCurrentVisibleRange({
          start: Math.max(
            0,
            messageHistory.length -
              (currentVisibleRange.end - currentVisibleRange.start)
          ),
          end: messageHistory.length,
          mostVisibleMessageId: currentVisibleRange.mostVisibleMessageId,
        });

        // Wait for the state update and re-render before scrolling
        setTimeout(() => {
          endDivRef.current?.scrollIntoView({
            behavior: fast ? "auto" : "smooth",
          });
          setHasPerformedInitialScroll(true);
        }, 100);
      } else {
        // If all messages are already rendered, scroll immediately
        endDivRef.current.scrollIntoView({
          behavior: fast ? "auto" : "smooth",
        });

        setHasPerformedInitialScroll(true);
      }
    }, 50);

    // Reset waitForScrollRef after 1.5 seconds
    setTimeout(() => {
      waitForScrollRef.current = false;
    }, 1500);
  };

  const debounceNumber = 100; // time for debouncing

  const [hasPerformedInitialScroll, setHasPerformedInitialScroll] = useState(
    existingChatSessionId === null
  );

  // handle re-sizing of the text area
  const textAreaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    handleInputResize();
  }, [message]);

  // tracks scrolling
  useEffect(() => {
    updateScrollTracking();
  }, [messageHistory]);

  // used for resizing of the document sidebar
  const masterFlexboxRef = useRef<HTMLDivElement>(null);
  const [maxDocumentSidebarWidth, setMaxDocumentSidebarWidth] = useState<
    number | null
  >(null);
  const adjustDocumentSidebarWidth = () => {
    if (masterFlexboxRef.current && document.documentElement.clientWidth) {
      // numbers below are based on the actual width the center section for different
      // screen sizes. `1700` corresponds to the custom "3xl" tailwind breakpoint
      // NOTE: some buffer is needed to account for scroll bars
      if (document.documentElement.clientWidth > 1700) {
        setMaxDocumentSidebarWidth(masterFlexboxRef.current.clientWidth - 950);
      } else if (document.documentElement.clientWidth > 1420) {
        setMaxDocumentSidebarWidth(masterFlexboxRef.current.clientWidth - 760);
      } else {
        setMaxDocumentSidebarWidth(masterFlexboxRef.current.clientWidth - 660);
      }
    }
  };

  useEffect(() => {
    if (
      (!personaIncludesRetrieval &&
        (!selectedDocuments || selectedDocuments.length === 0) &&
        documentSidebarToggled) ||
      chatSessionIdRef.current == undefined
    ) {
      setDocumentSidebarToggled(false);
    }
    clientScrollToBottom();
  }, [chatSessionIdRef.current]);

  const loadNewPageLogic = (event: MessageEvent) => {
    if (event.data.type === SUBMIT_MESSAGE_TYPES.PAGE_CHANGE) {
      try {
        const url = new URL(event.data.href);
        processSearchParamsAndSubmitMessage(url.searchParams.toString());
      } catch (error) {
        console.error("Error parsing URL:", error);
      }
    }
  };

  // Equivalent to `loadNewPageLogic`
  useEffect(() => {
    if (searchParams.get(SEARCH_PARAM_NAMES.SEND_ON_LOAD)) {
      processSearchParamsAndSubmitMessage(searchParams.toString());
    }
  }, [searchParams, router]);

  useEffect(() => {
    adjustDocumentSidebarWidth();
    window.addEventListener("resize", adjustDocumentSidebarWidth);
    window.addEventListener("message", loadNewPageLogic);

    return () => {
      window.removeEventListener("message", loadNewPageLogic);
      window.removeEventListener("resize", adjustDocumentSidebarWidth);
    };
  }, []);

  if (!documentSidebarInitialWidth && maxDocumentSidebarWidth) {
    documentSidebarInitialWidth = Math.min(700, maxDocumentSidebarWidth);
  }
  class CurrentMessageFIFO {
    private stack: PacketType[] = [];
    isComplete: boolean = false;
    error: string | null = null;

    push(packetBunch: PacketType) {
      this.stack.push(packetBunch);
    }

    nextPacket(): PacketType | undefined {
      return this.stack.shift();
    }

    isEmpty(): boolean {
      return this.stack.length === 0;
    }
  }

  async function updateCurrentMessageFIFO(
    stack: CurrentMessageFIFO,
    params: any
  ) {
    try {
      for await (const packet of sendMessage(params)) {
        if (params.signal?.aborted) {
          throw new Error("AbortError");
        }
        stack.push(packet);
      }
    } catch (error: unknown) {
      if (error instanceof Error) {
        if (error.name === "AbortError") {
          console.debug("Stream aborted");
        } else {
          stack.error = error.message;
        }
      } else {
        stack.error = String(error);
      }
    } finally {
      stack.isComplete = true;
    }
  }

  const resetInputBar = () => {
    setMessage("");
    setCurrentMessageFiles([]);
    if (endPaddingRef.current) {
      endPaddingRef.current.style.height = `95px`;
    }
  };

  const continueGenerating = () => {
    onSubmit({
      messageOverride:
        "Continue Generating (pick up exactly where you left off)",
    });
  };

  const onSubmit = async ({
    messageIdToResend,
    messageOverride,
    queryOverride,
    forceSearch,
    isSeededChat,
    alternativeAssistantOverride = null,
    modelOverRide,
    regenerationRequest,
    overrideFileDescriptors,
  }: {
    messageIdToResend?: number;
    messageOverride?: string;
    queryOverride?: string;
    forceSearch?: boolean;
    isSeededChat?: boolean;
    alternativeAssistantOverride?: Persona | null;
    modelOverRide?: LlmOverride;
    regenerationRequest?: RegenerationRequest | null;
    overrideFileDescriptors?: FileDescriptor[];
  } = {}) => {
    let frozenSessionId = currentSessionId();
    updateCanContinue(false, frozenSessionId);

    if (currentChatState() != "input") {
      if (currentChatState() == "uploading") {
        setPopup({
          message: "Please wait for the content to upload",
          type: "error",
        });
      } else {
        setPopup({
          message: "Please wait for the response to complete",
          type: "error",
        });
      }

      return;
    }

    setAlternativeGeneratingAssistant(alternativeAssistantOverride);

    clientScrollToBottom();

    let currChatSessionId: string;
    const isNewSession = chatSessionIdRef.current === null;

    const searchParamBasedChatSessionName =
      searchParams.get(SEARCH_PARAM_NAMES.TITLE) || null;

    if (isNewSession) {
      currChatSessionId = await createChatSession(
        liveAssistant?.id || 0,
        searchParamBasedChatSessionName
      );
    } else {
      currChatSessionId = chatSessionIdRef.current as string;
    }
    frozenSessionId = currChatSessionId;

    updateStatesWithNewSessionId(currChatSessionId);

    const controller = new AbortController();

    setAbortControllers((prev) =>
      new Map(prev).set(currChatSessionId, controller)
    );

    const messageToResend = messageHistory.find(
      (message) => message.messageId === messageIdToResend
    );
    if (messageIdToResend) {
      updateRegenerationState(
        { regenerating: true, finalMessageIndex: messageIdToResend },
        currentSessionId()
      );
    }
    const messageMap = currentMessageMap(completeMessageDetail);
    const messageToResendParent =
      messageToResend?.parentMessageId !== null &&
      messageToResend?.parentMessageId !== undefined
        ? messageMap.get(messageToResend.parentMessageId)
        : null;
    const messageToResendIndex = messageToResend
      ? messageHistory.indexOf(messageToResend)
      : null;

    if (!messageToResend && messageIdToResend !== undefined) {
      setPopup({
        message:
          "Failed to re-send message - please refresh the page and try again.",
        type: "error",
      });
      resetRegenerationState(currentSessionId());
      updateChatState("input", frozenSessionId);
      return;
    }
    let currMessage = messageToResend ? messageToResend.message : message;
    if (messageOverride) {
      currMessage = messageOverride;
    }

    setSubmittedMessage(currMessage);

    updateChatState("loading");

    const currMessageHistory =
      messageToResendIndex !== null
        ? messageHistory.slice(0, messageToResendIndex)
        : messageHistory;

    let parentMessage =
      messageToResendParent ||
      (currMessageHistory.length > 0
        ? currMessageHistory[currMessageHistory.length - 1]
        : null) ||
      (messageMap.size === 1 ? Array.from(messageMap.values())[0] : null);

    const currentAssistantId = alternativeAssistantOverride
      ? alternativeAssistantOverride.id
      : alternativeAssistant
        ? alternativeAssistant.id
        : liveAssistant.id;

    resetInputBar();
    let messageUpdates: Message[] | null = null;

    let answer = "";
    let second_level_answer = "";

    const stopReason: StreamStopReason | null = null;
    let query: string | null = null;
    let retrievalType: RetrievalType =
      selectedDocuments.length > 0
        ? RetrievalType.SelectedDocs
        : RetrievalType.None;
    let documents: OnyxDocument[] = selectedDocuments;
    let aiMessageImages: FileDescriptor[] | null = null;
    let agenticDocs: OnyxDocument[] | null = null;
    let error: string | null = null;
    let stackTrace: string | null = null;

    let sub_questions: SubQuestionDetail[] = [];
    let second_level_sub_questions: SubQuestionDetail[] = [];
    let is_generating: boolean = false;
    let second_level_generating: boolean = false;
    let finalMessage: BackendMessage | null = null;
    let toolCall: ToolCallMetadata | null = null;
    let isImprovement: boolean | undefined = undefined;

    let initialFetchDetails: null | {
      user_message_id: number;
      assistant_message_id: number;
      frozenMessageMap: Map<number, Message>;
    } = null;
    try {
      const mapKeys = Array.from(
        currentMessageMap(completeMessageDetail).keys()
      );
      const systemMessage = Math.min(...mapKeys);

      const lastSuccessfulMessageId =
        getLastSuccessfulMessageId(currMessageHistory) || systemMessage;

      const stack = new CurrentMessageFIFO();
      updateCurrentMessageFIFO(stack, {
        signal: controller.signal, // Add this line
        message: currMessage,
        alternateAssistantId: currentAssistantId,
        fileDescriptors: overrideFileDescriptors || currentMessageFiles,
        parentMessageId:
          regenerationRequest?.parentMessage.messageId ||
          lastSuccessfulMessageId,
        chatSessionId: currChatSessionId,
        promptId: liveAssistant?.prompts[0]?.id || 0,
        filters: buildFilters(
          filterManager.selectedSources,
          filterManager.selectedDocumentSets,
          filterManager.timeRange,
          filterManager.selectedTags
        ),
        selectedDocumentIds: selectedDocuments
          .filter(
            (document) =>
              document.db_doc_id !== undefined && document.db_doc_id !== null
          )
          .map((document) => document.db_doc_id as number),
        queryOverride,
        forceSearch,
        regenerate: regenerationRequest !== undefined,
        modelProvider:
          modelOverRide?.name ||
          llmOverrideManager.llmOverride.name ||
          undefined,
        modelVersion:
          modelOverRide?.modelName ||
          llmOverrideManager.llmOverride.modelName ||
          searchParams.get(SEARCH_PARAM_NAMES.MODEL_VERSION) ||
          undefined,
        temperature: llmOverrideManager.temperature || undefined,
        systemPromptOverride:
          searchParams.get(SEARCH_PARAM_NAMES.SYSTEM_PROMPT) || undefined,
        useExistingUserMessage: isSeededChat,
        useLanggraph:
          !settings?.settings.pro_search_disabled &&
          proSearchEnabled &&
          retrievalEnabled,
      });

      const delay = (ms: number) => {
        return new Promise((resolve) => setTimeout(resolve, ms));
      };

      await delay(50);
      while (!stack.isComplete || !stack.isEmpty()) {
        if (stack.isEmpty()) {
          await delay(0.5);
        }

        if (!stack.isEmpty() && !controller.signal.aborted) {
          const packet = stack.nextPacket();
          if (!packet) {
            continue;
          }

          if (!initialFetchDetails) {
            if (!Object.hasOwn(packet, "user_message_id")) {
              console.error(
                "First packet should contain message response info "
              );
              if (Object.hasOwn(packet, "error")) {
                const error = (packet as StreamingError).error;
                setLoadingError(error);
                updateChatState("input");
                return;
              }
              continue;
            }

            const messageResponseIDInfo = packet as MessageResponseIDInfo;

            const user_message_id = messageResponseIDInfo.user_message_id!;
            const assistant_message_id =
              messageResponseIDInfo.reserved_assistant_message_id;

            // we will use tempMessages until the regenerated message is complete
            messageUpdates = [
              {
                messageId: regenerationRequest
                  ? regenerationRequest?.parentMessage?.messageId!
                  : user_message_id,
                message: currMessage,
                type: "user",
                files: currentMessageFiles,
                toolCall: null,
                parentMessageId: parentMessage?.messageId || SYSTEM_MESSAGE_ID,
              },
            ];

            if (parentMessage && !regenerationRequest) {
              messageUpdates.push({
                ...parentMessage,
                childrenMessageIds: (
                  parentMessage.childrenMessageIds || []
                ).concat([user_message_id]),
                latestChildMessageId: user_message_id,
              });
            }

            const { messageMap: currentFrozenMessageMap } =
              upsertToCompleteMessageMap({
                messages: messageUpdates,
                chatSessionId: currChatSessionId,
              });

            const frozenMessageMap = currentFrozenMessageMap;
            initialFetchDetails = {
              frozenMessageMap,
              assistant_message_id,
              user_message_id,
            };

            resetRegenerationState();
          } else {
            const { user_message_id, frozenMessageMap } = initialFetchDetails;

            setChatState((prevState) => {
              if (prevState.get(chatSessionIdRef.current!) === "loading") {
                return new Map(prevState).set(
                  chatSessionIdRef.current!,
                  "streaming"
                );
              }
              return prevState;
            });

            if (Object.hasOwn(packet, "level")) {
              if ((packet as any).level === 1) {
                second_level_generating = true;
              }
            }

            if (Object.hasOwn(packet, "refined_answer_improvement")) {
              isImprovement = (packet as RefinedAnswerImprovement)
                .refined_answer_improvement;
            }

            if (Object.hasOwn(packet, "stream_type")) {
              if ((packet as any).stream_type == "main_answer") {
                is_generating = false;
                second_level_generating = true;
              }
            }

            // // Continuously refine the sub_questions based on the packets that we receive
            if (
              Object.hasOwn(packet, "stop_reason") &&
              Object.hasOwn(packet, "level_question_num")
            ) {
              sub_questions = constructSubQuestions(
                sub_questions,
                packet as StreamStopInfo
              );
            } else if (Object.hasOwn(packet, "sub_question")) {
              is_generating = true;
              sub_questions = constructSubQuestions(
                sub_questions,
                packet as SubQuestionPiece
              );
              setAgenticGenerating(true);
            } else if (Object.hasOwn(packet, "sub_query")) {
              sub_questions = constructSubQuestions(
                sub_questions,
                packet as SubQueryPiece
              );
            } else if (
              Object.hasOwn(packet, "answer_piece") &&
              Object.hasOwn(packet, "answer_type") &&
              (packet as AgentAnswerPiece).answer_type === "agent_sub_answer"
            ) {
              sub_questions = constructSubQuestions(
                sub_questions,
                packet as AgentAnswerPiece
              );
            } else if (Object.hasOwn(packet, "answer_piece")) {
              // Mark every sub_question's is_generating as false
              sub_questions = sub_questions.map((subQ) => ({
                ...subQ,
                is_generating: false,
              }));

              if (
                Object.hasOwn(packet, "level") &&
                (packet as any).level === 1
              ) {
                second_level_answer += (packet as AnswerPiecePacket)
                  .answer_piece;
              } else {
                answer += (packet as AnswerPiecePacket).answer_piece;
              }
            } else if (
              Object.hasOwn(packet, "top_documents") &&
              Object.hasOwn(packet, "level_question_num") &&
              (packet as DocumentsResponse).level_question_num != undefined
            ) {
              const documentsResponse = packet as DocumentsResponse;
              sub_questions = constructSubQuestions(
                sub_questions,
                documentsResponse
              );

              if (
                documentsResponse.level_question_num === 0 &&
                documentsResponse.level == 0
              ) {
                documents = (packet as DocumentsResponse).top_documents;
              } else if (
                documentsResponse.level_question_num === 0 &&
                documentsResponse.level == 1
              ) {
                agenticDocs = (packet as DocumentsResponse).top_documents;
              }
            } else if (Object.hasOwn(packet, "top_documents")) {
              documents = (packet as DocumentInfoPacket).top_documents;
              retrievalType = RetrievalType.Search;

              if (documents && documents.length > 0) {
                // point to the latest message (we don't know the messageId yet, which is why
                // we have to use -1)
                setSelectedMessageForDocDisplay(user_message_id);
              }
            } else if (Object.hasOwn(packet, "tool_name")) {
              // Will only ever be one tool call per message
              toolCall = {
                tool_name: (packet as ToolCallMetadata).tool_name,
                tool_args: (packet as ToolCallMetadata).tool_args,
                tool_result: (packet as ToolCallMetadata).tool_result,
              };

              if (!toolCall.tool_name.includes("agent")) {
                if (
                  !toolCall.tool_result ||
                  toolCall.tool_result == undefined
                ) {
                  updateChatState("toolBuilding", frozenSessionId);
                } else {
                  updateChatState("streaming", frozenSessionId);
                }

                // This will be consolidated in upcoming tool calls udpate,
                // but for now, we need to set query as early as possible
                if (toolCall.tool_name == SEARCH_TOOL_NAME) {
                  query = toolCall.tool_args["query"];
                }
              } else {
                toolCall = null;
              }
            } else if (Object.hasOwn(packet, "file_ids")) {
              aiMessageImages = (packet as FileChatDisplay).file_ids.map(
                (fileId) => {
                  return {
                    id: fileId,
                    type: ChatFileType.IMAGE,
                  };
                }
              );
            } else if (Object.hasOwn(packet, "error")) {
              error = (packet as StreamingError).error;
              stackTrace = (packet as StreamingError).stack_trace;
            } else if (Object.hasOwn(packet, "message_id")) {
              finalMessage = packet as BackendMessage;
            } else if (Object.hasOwn(packet, "stop_reason")) {
              const stop_reason = (packet as StreamStopInfo).stop_reason;
              if (stop_reason === StreamStopReason.CONTEXT_LENGTH) {
                updateCanContinue(true, frozenSessionId);
              }
            }

            // on initial message send, we insert a dummy system message
            // set this as the parent here if no parent is set
            parentMessage =
              parentMessage || frozenMessageMap?.get(SYSTEM_MESSAGE_ID)!;

            const updateFn = (messages: Message[]) => {
              const replacementsMap = regenerationRequest
                ? new Map([
                    [
                      regenerationRequest?.parentMessage?.messageId,
                      regenerationRequest?.parentMessage?.messageId,
                    ],
                    [
                      regenerationRequest?.messageId,
                      initialFetchDetails?.assistant_message_id,
                    ],
                  ] as [number, number][])
                : null;

              return upsertToCompleteMessageMap({
                messages: messages,
                replacementsMap: replacementsMap,
                completeMessageMapOverride: frozenMessageMap,
                chatSessionId: frozenSessionId!,
              });
            };

            updateFn([
              {
                messageId: regenerationRequest
                  ? regenerationRequest?.parentMessage?.messageId!
                  : initialFetchDetails.user_message_id!,
                message: currMessage,
                type: "user",
                files: currentMessageFiles,
                toolCall: null,
                parentMessageId: error ? null : lastSuccessfulMessageId,
                childrenMessageIds: [
                  ...(regenerationRequest?.parentMessage?.childrenMessageIds ||
                    []),
                  initialFetchDetails.assistant_message_id!,
                ],
                latestChildMessageId: initialFetchDetails.assistant_message_id,
              },
              {
                is_generating: is_generating,
                isImprovement: isImprovement,
                messageId: initialFetchDetails.assistant_message_id!,
                message: error || answer,
                second_level_message: second_level_answer,
                type: error ? "error" : "assistant",
                retrievalType,
                query: finalMessage?.rephrased_query,
                documents: documents,
                citations: finalMessage?.citations || {},
                files: finalMessage?.files || aiMessageImages || [],
                toolCall: finalMessage?.tool_call || toolCall,
                parentMessageId: regenerationRequest
                  ? regenerationRequest?.parentMessage?.messageId!
                  : initialFetchDetails.user_message_id,
                alternateAssistantID: alternativeAssistant?.id,
                stackTrace: stackTrace,
                overridden_model: finalMessage?.overridden_model,
                stopReason: stopReason,
                sub_questions: sub_questions,
                second_level_generating: second_level_generating,
                agentic_docs: agenticDocs,
              },
            ]);
          }
        }
      }
    } catch (e: any) {
      const errorMsg = e.message;
      upsertToCompleteMessageMap({
        messages: [
          {
            messageId:
              initialFetchDetails?.user_message_id || TEMP_USER_MESSAGE_ID,
            message: currMessage,
            type: "user",
            files: currentMessageFiles,
            toolCall: null,
            parentMessageId: parentMessage?.messageId || SYSTEM_MESSAGE_ID,
          },
          {
            messageId:
              initialFetchDetails?.assistant_message_id ||
              TEMP_ASSISTANT_MESSAGE_ID,
            message: errorMsg,
            type: "error",
            files: aiMessageImages || [],
            toolCall: null,
            parentMessageId:
              initialFetchDetails?.user_message_id || TEMP_USER_MESSAGE_ID,
          },
        ],
        completeMessageMapOverride: currentMessageMap(completeMessageDetail),
      });
    }
    setAgenticGenerating(false);
    resetRegenerationState(currentSessionId());

    updateChatState("input");
    if (isNewSession) {
      if (finalMessage) {
        setSelectedMessageForDocDisplay(finalMessage.message_id);
      }

      if (!searchParamBasedChatSessionName) {
        await new Promise((resolve) => setTimeout(resolve, 200));
        await nameChatSession(currChatSessionId);
        refreshChatSessions();
      }

      // NOTE: don't switch pages if the user has navigated away from the chat
      if (
        currChatSessionId === chatSessionIdRef.current ||
        chatSessionIdRef.current === null
      ) {
        const newUrl = buildChatUrl(searchParams, currChatSessionId, null);
        // newUrl is like /chat?chatId=10
        // current page is like /chat
        router.push(newUrl, { scroll: false });
      }
    }
    if (
      finalMessage?.context_docs &&
      finalMessage.context_docs.top_documents.length > 0 &&
      retrievalType === RetrievalType.Search
    ) {
      setSelectedMessageForDocDisplay(finalMessage.message_id);
    }
    setAlternativeGeneratingAssistant(null);
    setSubmittedMessage("");
  };

  const onFeedback = async (
    messageId: number,
    feedbackType: FeedbackType,
    feedbackDetails: string,
    predefinedFeedback: string | undefined
  ) => {
    if (chatSessionIdRef.current === null) {
      return;
    }

    const response = await handleChatFeedback(
      messageId,
      feedbackType,
      feedbackDetails,
      predefinedFeedback
    );

    if (response.ok) {
      setPopup({
        message: "Thanks for your feedback!",
        type: "success",
      });
    } else {
      const responseJson = await response.json();
      const errorMsg = responseJson.detail || responseJson.message;
      setPopup({
        message: `Failed to submit feedback - ${errorMsg}`,
        type: "error",
      });
    }
  };

  const handleImageUpload = async (acceptedFiles: File[]) => {
    const [_, llmModel] = getFinalLLM(
      llmProviders,
      liveAssistant,
      llmOverrideManager.llmOverride
    );
    const llmAcceptsImages = checkLLMSupportsImageInput(llmModel);

    const imageFiles = acceptedFiles.filter((file) =>
      file.type.startsWith("image/")
    );

    if (imageFiles.length > 0 && !llmAcceptsImages) {
      setPopup({
        type: "error",
        message:
          "The current model does not support image input. Please select a model with Vision support.",
      });
      return;
    }

    const tempFileDescriptors = acceptedFiles.map((file) => ({
      id: uuidv4(),
      type: file.type.startsWith("image/")
        ? ChatFileType.IMAGE
        : ChatFileType.DOCUMENT,
      isUploading: true,
    }));

    // only show loading spinner for reasonably large files
    const totalSize = acceptedFiles.reduce((sum, file) => sum + file.size, 0);
    if (totalSize > 50 * 1024) {
      setCurrentMessageFiles((prev) => [...prev, ...tempFileDescriptors]);
    }

    const removeTempFiles = (prev: FileDescriptor[]) => {
      return prev.filter(
        (file) => !tempFileDescriptors.some((newFile) => newFile.id === file.id)
      );
    };
    updateChatState("uploading", currentSessionId());

    await uploadFilesForChat(acceptedFiles).then(([files, error]) => {
      if (error) {
        setCurrentMessageFiles((prev) => removeTempFiles(prev));
        setPopup({
          type: "error",
          message: error,
        });
      } else {
        setCurrentMessageFiles((prev) => [...removeTempFiles(prev), ...files]);
      }
    });
    updateChatState("input", currentSessionId());
  };

  // Used to maintain a "time out" for history sidebar so our existing refs can have time to process change
  const [untoggled, setUntoggled] = useState(false);
  const [loadingError, setLoadingError] = useState<string | null>(null);
  const [agenticGenerating, setAgenticGenerating] = useState(false);

  const explicitlyUntoggle = () => {
    setShowHistorySidebar(false);

    setUntoggled(true);
    setTimeout(() => {
      setUntoggled(false);
    }, 200);
  };
  const toggleSidebar = () => {
    if (user?.is_anonymous_user) {
      return;
    }
    Cookies.set(
      SIDEBAR_TOGGLED_COOKIE_NAME,
      String(!toggledSidebar).toLocaleLowerCase()
    ),
      {
        path: "/",
      };

    toggle();
  };
  const removeToggle = () => {
    setShowHistorySidebar(false);
    toggle(false);
  };

  const waitForScrollRef = useRef(false);
  const sidebarElementRef = useRef<HTMLDivElement>(null);

  useSidebarVisibility({
    toggledSidebar,
    sidebarElementRef,
    showDocSidebar: showHistorySidebar,
    setShowDocSidebar: setShowHistorySidebar,
    setToggled: removeToggle,
    mobile: settings?.isMobile,
    isAnonymousUser: user?.is_anonymous_user,
  });

  const autoScrollEnabled =
    user?.preferences?.auto_scroll == null
      ? settings?.enterpriseSettings?.auto_scroll || false
      : user?.preferences?.auto_scroll! && !agenticGenerating;

  useScrollonStream({
    chatState: currentSessionChatState,
    scrollableDivRef,
    scrollDist,
    endDivRef,
    debounceNumber,
    mobile: settings?.isMobile,
    enableAutoScroll: autoScrollEnabled,
  });

  // Virtualization + Scrolling related effects and functions
  const scrollInitialized = useRef(false);
  interface VisibleRange {
    start: number;
    end: number;
    mostVisibleMessageId: number | null;
  }

  const [visibleRange, setVisibleRange] = useState<
    Map<string | null, VisibleRange>
  >(() => {
    const initialRange: VisibleRange = {
      start: 0,
      end: BUFFER_COUNT,
      mostVisibleMessageId: null,
    };
    return new Map([[chatSessionIdRef.current, initialRange]]);
  });

  // Function used to update current visible range. Only method for updating `visibleRange` state.
  const updateCurrentVisibleRange = (
    newRange: VisibleRange,
    forceUpdate?: boolean
  ) => {
    if (
      scrollInitialized.current &&
      visibleRange.get(loadedIdSessionRef.current) == undefined &&
      !forceUpdate
    ) {
      return;
    }

    setVisibleRange((prevState) => {
      const newState = new Map(prevState);
      newState.set(loadedIdSessionRef.current, newRange);
      return newState;
    });
  };

  //  Set first value for visibleRange state on page load / refresh.
  const initializeVisibleRange = () => {
    const upToDatemessageHistory = buildLatestMessageChain(
      currentMessageMap(completeMessageDetail)
    );

    if (!scrollInitialized.current && upToDatemessageHistory.length > 0) {
      const newEnd = Math.max(upToDatemessageHistory.length, BUFFER_COUNT);
      const newStart = Math.max(0, newEnd - BUFFER_COUNT);
      const newMostVisibleMessageId =
        upToDatemessageHistory[newEnd - 1]?.messageId;

      updateCurrentVisibleRange(
        {
          start: newStart,
          end: newEnd,
          mostVisibleMessageId: newMostVisibleMessageId,
        },
        true
      );
      scrollInitialized.current = true;
    }
  };

  const updateVisibleRangeBasedOnScroll = () => {
    if (!scrollInitialized.current) return;
    const scrollableDiv = scrollableDivRef.current;
    if (!scrollableDiv) return;

    const viewportHeight = scrollableDiv.clientHeight;
    let mostVisibleMessageIndex = -1;

    messageHistory.forEach((message, index) => {
      const messageElement = document.getElementById(
        `message-${message.messageId}`
      );
      if (messageElement) {
        const rect = messageElement.getBoundingClientRect();
        const isVisible = rect.bottom <= viewportHeight && rect.bottom > 0;
        if (isVisible && index > mostVisibleMessageIndex) {
          mostVisibleMessageIndex = index;
        }
      }
    });

    if (mostVisibleMessageIndex !== -1) {
      const startIndex = Math.max(0, mostVisibleMessageIndex - BUFFER_COUNT);
      const endIndex = Math.min(
        messageHistory.length,
        mostVisibleMessageIndex + BUFFER_COUNT + 1
      );

      updateCurrentVisibleRange({
        start: startIndex,
        end: endIndex,
        mostVisibleMessageId: messageHistory[mostVisibleMessageIndex].messageId,
      });
    }
  };

  useEffect(() => {
    initializeVisibleRange();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, messageHistory]);

  useLayoutEffect(() => {
    const scrollableDiv = scrollableDivRef.current;

    const handleScroll = () => {
      updateVisibleRangeBasedOnScroll();
    };

    scrollableDiv?.addEventListener("scroll", handleScroll);

    return () => {
      scrollableDiv?.removeEventListener("scroll", handleScroll);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messageHistory]);

  const imageFileInMessageHistory = useMemo(() => {
    return messageHistory
      .filter((message) => message.type === "user")
      .some((message) =>
        message.files.some((file) => file.type === ChatFileType.IMAGE)
      );
  }, [messageHistory]);

  const currentVisibleRange = visibleRange.get(currentSessionId()) || {
    start: 0,
    end: 0,
    mostVisibleMessageId: null,
  };
  useSendMessageToParent();

  useEffect(() => {
    if (liveAssistant) {
      const hasSearchTool = liveAssistant.tools.some(
        (tool) => tool.in_code_tool_id === SEARCH_TOOL_ID
      );
      setRetrievalEnabled(hasSearchTool);
      if (!hasSearchTool) {
        filterManager.clearFilters();
      }
    }
  }, [liveAssistant]);

  const [retrievalEnabled, setRetrievalEnabled] = useState(() => {
    if (liveAssistant) {
      return liveAssistant.tools.some(
        (tool) => tool.in_code_tool_id === SEARCH_TOOL_ID
      );
    }
    return false;
  });

  useEffect(() => {
    if (!retrievalEnabled) {
      setDocumentSidebarToggled(false);
    }
  }, [retrievalEnabled]);

  const [stackTraceModalContent, setStackTraceModalContent] = useState<
    string | null
  >(null);

  const innerSidebarElementRef = useRef<HTMLDivElement>(null);
  const [settingsToggled, setSettingsToggled] = useState(false);
  const [showDeleteAllModal, setShowDeleteAllModal] = useState(false);

  const currentPersona = alternativeAssistant || liveAssistant;

  useEffect(() => {
    const handleSlackChatRedirect = async () => {
      if (!slackChatId) return;

      // Set isReady to false before starting retrieval to display loading text
      setIsReady(false);

      try {
        const response = await fetch("/api/chat/seed-chat-session-from-slack", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            chat_session_id: slackChatId,
          }),
        });

        if (!response.ok) {
          throw new Error("Failed to seed chat from Slack");
        }

        const data = await response.json();
        router.push(data.redirect_url);
      } catch (error) {
        console.error("Error seeding chat from Slack:", error);
        setPopup({
          message: "Failed to load chat from Slack",
          type: "error",
        });
      }
    };

    handleSlackChatRedirect();
  }, [searchParams, router]);

  useEffect(() => {
    llmOverrideManager.updateImageFilesPresent(imageFileInMessageHistory);
  }, [imageFileInMessageHistory]);

  useSidebarShortcut(router, toggleSidebar);

  const [sharedChatSession, setSharedChatSession] =
    useState<ChatSession | null>();

  const showShareModal = (chatSession: ChatSession) => {
    setSharedChatSession(chatSession);
  };
  const [showAssistantsModal, setShowAssistantsModal] = useState(false);

  const toggleDocumentSidebar = () => {
    if (!documentSidebarToggled) {
      setDocumentSidebarToggled(true);
    } else {
      setDocumentSidebarToggled(false);
    }
  };

  interface RegenerationRequest {
    messageId: number;
    parentMessage: Message;
    forceSearch?: boolean;
  }

  function createRegenerator(regenerationRequest: RegenerationRequest) {
    // Returns new function that only needs `modelOverRide` to be specified when called
    return async function (modelOverRide: LlmOverride) {
      return await onSubmit({
        modelOverRide,
        messageIdToResend: regenerationRequest.parentMessage.messageId,
        regenerationRequest,
        forceSearch: regenerationRequest.forceSearch,
      });
    };
  }
  if (!user) {
    redirect("/auth/login");
  }

  if (noAssistants)
    return (
      <>
        <HealthCheckBanner />
        <NoAssistantModal isAdmin={isAdmin} />
      </>
    );

  return (
    <>
      <HealthCheckBanner />

      {showApiKeyModal && !shouldShowWelcomeModal && (
        <ApiKeyModal
          hide={() => setShowApiKeyModal(false)}
          setPopup={setPopup}
        />
      )}

      {/* ChatPopup is a custom popup that displays a admin-specified message on initial user visit. 
      Only used in the EE version of the app. */}
      {popup}

      <ChatPopup />

      {showDeleteAllModal && (
        <DeleteEntityModal
          entityType="All Chats"
          entityName="all your chat sessions"
          onClose={() => setShowDeleteAllModal(false)}
          additionalDetails="This action cannot be undone. All your chat sessions will be deleted."
          onSubmit={async () => {
            const response = await deleteAllChatSessions("Chat");
            if (response.ok) {
              setShowDeleteAllModal(false);
              setPopup({
                message: "All your chat sessions have been deleted.",
                type: "success",
              });
              refreshChatSessions();
              router.push("/chat");
            } else {
              setPopup({
                message: "Failed to delete all chat sessions.",
                type: "error",
              });
            }
          }}
        />
      )}

      {currentFeedback && (
        <FeedbackModal
          feedbackType={currentFeedback[0]}
          onClose={() => setCurrentFeedback(null)}
          onSubmit={({ message, predefinedFeedback }) => {
            onFeedback(
              currentFeedback[1],
              currentFeedback[0],
              message,
              predefinedFeedback
            );
            setCurrentFeedback(null);
          }}
        />
      )}

      {(settingsToggled || userSettingsToggled) && (
        <UserSettingsModal
          setPopup={setPopup}
          setLlmOverride={(newOverride) =>
            llmOverrideManager.updateLLMOverride(newOverride)
          }
          defaultModel={user?.preferences.default_model!}
          llmProviders={llmProviders}
          onClose={() => {
            setUserSettingsToggled(false);
            setSettingsToggled(false);
          }}
        />
      )}

      {retrievalEnabled && documentSidebarToggled && settings?.isMobile && (
        <div className="md:hidden">
          <Modal
            hideDividerForTitle
            onOutsideClick={() => setDocumentSidebarToggled(false)}
            title="Sources"
          >
            <DocumentResults
              agenticMessage={
                aiMessage?.sub_questions?.length! > 0 ||
                messageHistory.find(
                  (m) => m.messageId === aiMessage?.parentMessageId
                )?.sub_questions?.length! > 0
                  ? true
                  : false
              }
              setPresentingDocument={setPresentingDocument}
              modal={true}
              ref={innerSidebarElementRef}
              closeSidebar={() => {
                setDocumentSidebarToggled(false);
              }}
              selectedMessage={aiMessage}
              selectedDocuments={selectedDocuments}
              toggleDocumentSelection={toggleDocumentSelection}
              clearSelectedDocuments={clearSelectedDocuments}
              selectedDocumentTokens={selectedDocumentTokens}
              maxTokens={maxTokens}
              initialWidth={400}
              isOpen={true}
              removeHeader
            />
          </Modal>
        </div>
      )}

      {presentingDocument && (
        <TextView
          presentingDocument={presentingDocument}
          onClose={() => setPresentingDocument(null)}
        />
      )}

      {stackTraceModalContent && (
        <ExceptionTraceModal
          onOutsideClick={() => setStackTraceModalContent(null)}
          exceptionTrace={stackTraceModalContent}
        />
      )}

      {sharedChatSession && (
        <ShareChatSessionModal
          assistantId={liveAssistant?.id}
          message={message}
          modelOverride={llmOverrideManager.llmOverride}
          chatSessionId={sharedChatSession.id}
          existingSharedStatus={sharedChatSession.shared_status}
          onClose={() => setSharedChatSession(null)}
          onShare={(shared) =>
            setChatSessionSharedStatus(
              shared
                ? ChatSessionSharedStatus.Public
                : ChatSessionSharedStatus.Private
            )
          }
        />
      )}

      {sharingModalVisible && chatSessionIdRef.current !== null && (
        <ShareChatSessionModal
          message={message}
          assistantId={liveAssistant?.id}
          modelOverride={llmOverrideManager.llmOverride}
          chatSessionId={chatSessionIdRef.current}
          existingSharedStatus={chatSessionSharedStatus}
          onClose={() => setSharingModalVisible(false)}
        />
      )}

      {showAssistantsModal && (
        <AssistantModal hideModal={() => setShowAssistantsModal(false)} />
      )}

      <div className="fixed inset-0 flex flex-col text-text-dark">
        <div className="h-[100dvh] overflow-y-hidden">
          <div className="w-full">
            <div
              ref={sidebarElementRef}
              className={`
                flex-none
                fixed
                left-0
                z-40
                bg-background-100
                h-screen
                transition-all
                bg-opacity-80
                duration-300
                ease-in-out
                ${
                  !untoggled && (showHistorySidebar || toggledSidebar)
                    ? "opacity-100 w-[250px] translate-x-0"
                    : "opacity-0 w-[250px] pointer-events-none -translate-x-10"
                }`}
            >
              <div className="w-full relative">
                <HistorySidebar
                  setShowAssistantsModal={setShowAssistantsModal}
                  explicitlyUntoggle={explicitlyUntoggle}
                  reset={() => setMessage("")}
                  page="chat"
                  ref={innerSidebarElementRef}
                  toggleSidebar={toggleSidebar}
                  toggled={toggledSidebar}
                  currentAssistantId={liveAssistant?.id}
                  existingChats={chatSessions}
                  currentChatSession={selectedChatSession}
                  folders={folders}
                  removeToggle={removeToggle}
                  showShareModal={showShareModal}
                  showDeleteAllModal={() => setShowDeleteAllModal(true)}
                />
              </div>
              <div
                className={`
                flex-none
                fixed
                left-0
                z-40
                bg-background-100
                h-screen
                transition-all
                bg-opacity-80
                duration-300
                ease-in-out
                ${
                  documentSidebarToggled &&
                  !settings?.isMobile &&
                  "opacity-100 w-[350px]"
                }`}
              ></div>
            </div>
          </div>

          <div
            style={{ transition: "width 0.30s ease-out" }}
            className={`
                flex-none 
                fixed
                right-0
                z-[1000]
                h-screen
                transition-all
                duration-300
                ease-in-out
                bg-transparent
                transition-all
                duration-300
                ease-in-out
                h-full
                ${
                  documentSidebarToggled && !settings?.isMobile
                    ? "w-[400px]"
                    : "w-[0px]"
                }
            `}
          >
            <DocumentResults
              agenticMessage={
                aiMessage?.sub_questions?.length! > 0 ||
                messageHistory.find(
                  (m) => m.messageId === aiMessage?.parentMessageId
                )?.sub_questions?.length! > 0
                  ? true
                  : false
              }
              setPresentingDocument={setPresentingDocument}
              modal={false}
              ref={innerSidebarElementRef}
              closeSidebar={() =>
                setTimeout(() => setDocumentSidebarToggled(false), 300)
              }
              selectedMessage={aiMessage}
              selectedDocuments={selectedDocuments}
              toggleDocumentSelection={toggleDocumentSelection}
              clearSelectedDocuments={clearSelectedDocuments}
              selectedDocumentTokens={selectedDocumentTokens}
              maxTokens={maxTokens}
              initialWidth={400}
              isOpen={documentSidebarToggled && !settings?.isMobile}
            />
          </div>

          <BlurBackground
            visible={!untoggled && (showHistorySidebar || toggledSidebar)}
            onClick={() => toggleSidebar()}
          />

          <div
            ref={masterFlexboxRef}
            className="flex h-full w-full overflow-x-hidden"
          >
            <div
              id="scrollableContainer"
              className="flex h-full relative px-2 flex-col w-full"
            >
              {liveAssistant && (
                <FunctionalHeader
                  toggleUserSettings={() => setUserSettingsToggled(true)}
                  sidebarToggled={toggledSidebar}
                  reset={() => setMessage("")}
                  page="chat"
                  setSharingModalVisible={
                    chatSessionIdRef.current !== null
                      ? setSharingModalVisible
                      : undefined
                  }
                  documentSidebarToggled={
                    documentSidebarToggled && !settings?.isMobile
                  }
                  toggleSidebar={toggleSidebar}
                  currentChatSession={selectedChatSession}
                  hideUserDropdown={user?.is_anonymous_user}
                />
              )}

              {documentSidebarInitialWidth !== undefined && isReady ? (
                <Dropzone
                  key={currentSessionId()}
                  onDrop={handleImageUpload}
                  noClick
                >
                  {({ getRootProps }) => (
                    <div className="flex h-full w-full">
                      {!settings?.isMobile && (
                        <div
                          style={{ transition: "width 0.30s ease-out" }}
                          className={`
                          flex-none 
                          overflow-y-hidden 
                          bg-transparent
                          transition-all 
                          bg-opacity-80
                          duration-300 
                          ease-in-out
                          h-full
                          ${toggledSidebar ? "w-[200px]" : "w-[0px]"}
                      `}
                        ></div>
                      )}

                      <div
                        className={`h-full w-full relative flex-auto transition-margin duration-300 overflow-x-auto mobile:pb-12 desktop:pb-[100px]`}
                        {...getRootProps()}
                      >
                        <div
                          className={`w-full h-[calc(100vh-160px)] flex flex-col default-scrollbar overflow-y-auto overflow-x-hidden relative`}
                          ref={scrollableDivRef}
                        >
                          {liveAssistant && (
                            <div className="z-20 fixed top-0 pointer-events-none left-0 w-full flex justify-center overflow-visible">
                              {!settings?.isMobile && (
                                <div
                                  style={{ transition: "width 0.30s ease-out" }}
                                  className={`
                                  flex-none 
                                  overflow-y-hidden 
                                  transition-all 
                                  pointer-events-none
                                  duration-300 
                                  ease-in-out
                                  h-full
                                  ${toggledSidebar ? "w-[200px]" : "w-[0px]"}
                              `}
                                ></div>
                              )}
                            </div>
                          )}
                          {/* ChatBanner is a custom banner that displays a admin-specified message at 
                      the top of the chat page. Oly used in the EE version of the app. */}
                          {messageHistory.length === 0 &&
                            !isFetchingChatMessages &&
                            currentSessionChatState == "input" &&
                            !loadingError &&
                            !submittedMessage && (
                              <div className="h-full  w-[95%] mx-auto flex flex-col justify-center items-center">
                                <ChatIntro selectedPersona={liveAssistant} />

                                <StarterMessages
                                  currentPersona={currentPersona}
                                  onSubmit={(messageOverride) =>
                                    onSubmit({
                                      messageOverride,
                                    })
                                  }
                                />
                              </div>
                            )}
                          <div
                            style={{ overflowAnchor: "none" }}
                            key={currentSessionId()}
                            className={
                              "desktop:-ml-4 w-full mx-auto " +
                              "absolute mobile:top-0 desktop:top-0 left-0 " +
                              (settings?.enterpriseSettings
                                ?.two_lines_for_chat_header
                                ? "pt-20 "
                                : "pt-4 ")
                            }
                            // NOTE: temporarily removing this to fix the scroll bug
                            // (hasPerformedInitialScroll ? "" : "invisible")
                          >
                            {(messageHistory.length < BUFFER_COUNT
                              ? messageHistory
                              : messageHistory.slice(
                                  currentVisibleRange.start,
                                  currentVisibleRange.end
                                )
                            ).map((message, fauxIndex) => {
                              const i =
                                messageHistory.length < BUFFER_COUNT
                                  ? fauxIndex
                                  : fauxIndex + currentVisibleRange.start;

                              const messageMap = currentMessageMap(
                                completeMessageDetail
                              );

                              if (
                                currentRegenerationState()?.finalMessageIndex &&
                                currentRegenerationState()?.finalMessageIndex! <
                                  message.messageId
                              ) {
                                return <></>;
                              }

                              const messageReactComponentKey = `${i}-${currentSessionId()}`;
                              const parentMessage = message.parentMessageId
                                ? messageMap.get(message.parentMessageId)
                                : null;
                              if (message.type === "user") {
                                if (
                                  (currentSessionChatState == "loading" &&
                                    i == messageHistory.length - 1) ||
                                  (currentSessionRegenerationState?.regenerating &&
                                    message.messageId >=
                                      currentSessionRegenerationState?.finalMessageIndex!)
                                ) {
                                  return <></>;
                                }
                                return (
                                  <div
                                    id={`message-${message.messageId}`}
                                    key={messageReactComponentKey}
                                  >
                                    <HumanMessage
                                      stopGenerating={stopGenerating}
                                      content={message.message}
                                      files={message.files}
                                      messageId={message.messageId}
                                      onEdit={(editedContent) => {
                                        const parentMessageId =
                                          message.parentMessageId!;
                                        const parentMessage =
                                          messageMap.get(parentMessageId)!;
                                        upsertToCompleteMessageMap({
                                          messages: [
                                            {
                                              ...parentMessage,
                                              latestChildMessageId: null,
                                            },
                                          ],
                                        });
                                        onSubmit({
                                          messageIdToResend:
                                            message.messageId || undefined,
                                          messageOverride: editedContent,
                                        });
                                      }}
                                      otherMessagesCanSwitchTo={
                                        parentMessage?.childrenMessageIds || []
                                      }
                                      onMessageSelection={(messageId) => {
                                        const newCompleteMessageMap = new Map(
                                          messageMap
                                        );
                                        newCompleteMessageMap.get(
                                          message.parentMessageId!
                                        )!.latestChildMessageId = messageId;
                                        updateCompleteMessageDetail(
                                          currentSessionId(),
                                          newCompleteMessageMap
                                        );
                                        setSelectedMessageForDocDisplay(
                                          messageId
                                        );
                                        // set message as latest so we can edit this message
                                        // and so it sticks around on page reload
                                        setMessageAsLatest(messageId);
                                      }}
                                    />
                                  </div>
                                );
                              } else if (message.type === "assistant") {
                                const previousMessage =
                                  i !== 0 ? messageHistory[i - 1] : null;

                                const currentAlternativeAssistant =
                                  message.alternateAssistantID != null
                                    ? availableAssistants.find(
                                        (persona) =>
                                          persona.id ==
                                          message.alternateAssistantID
                                      )
                                    : null;

                                if (
                                  (currentSessionChatState == "loading" &&
                                    i > messageHistory.length - 1) ||
                                  (currentSessionRegenerationState?.regenerating &&
                                    message.messageId >
                                      currentSessionRegenerationState?.finalMessageIndex!)
                                ) {
                                  return <></>;
                                }
                                if (parentMessage?.type == "assistant") {
                                  return <></>;
                                }
                                const secondLevelMessage =
                                  messageHistory[i + 1]?.type === "assistant"
                                    ? messageHistory[i + 1]
                                    : undefined;

                                const secondLevelAssistantMessage =
                                  messageHistory[i + 1]?.type === "assistant"
                                    ? messageHistory[i + 1]?.message
                                    : undefined;

                                const agenticDocs =
                                  messageHistory[i + 1]?.type === "assistant"
                                    ? messageHistory[i + 1]?.documents
                                    : undefined;

                                return (
                                  <div
                                    className="text-text"
                                    id={`message-${message.messageId}`}
                                    key={messageReactComponentKey}
                                    ref={
                                      i == messageHistory.length - 1
                                        ? lastMessageRef
                                        : null
                                    }
                                  >
                                    {message.sub_questions &&
                                    message.sub_questions.length > 0 ? (
                                      <AgenticMessage
                                        docSidebarToggled={
                                          documentSidebarToggled &&
                                          (selectedMessageForDocDisplay ==
                                            message.messageId ||
                                            selectedMessageForDocDisplay ==
                                              secondLevelMessage?.messageId)
                                        }
                                        isImprovement={message.isImprovement}
                                        secondLevelGenerating={
                                          (message.second_level_generating &&
                                            currentSessionChatState !==
                                              "input") ||
                                          false
                                        }
                                        secondLevelSubquestions={message.sub_questions?.filter(
                                          (subQuestion) =>
                                            subQuestion.level === 1
                                        )}
                                        secondLevelAssistantMessage={
                                          (message.second_level_message &&
                                          message.second_level_message.length >
                                            0
                                            ? message.second_level_message
                                            : secondLevelAssistantMessage) ||
                                          undefined
                                        }
                                        subQuestions={
                                          message.sub_questions?.filter(
                                            (subQuestion) =>
                                              subQuestion.level === 0
                                          ) || []
                                        }
                                        agenticDocs={
                                          message.agentic_docs || agenticDocs
                                        }
                                        toggleDocDisplay={(
                                          agentic: boolean
                                        ) => {
                                          if (agentic) {
                                            setSelectedMessageForDocDisplay(
                                              message.messageId
                                            );
                                          } else {
                                            setSelectedMessageForDocDisplay(
                                              secondLevelMessage
                                                ? secondLevelMessage.messageId
                                                : null
                                            );
                                          }
                                        }}
                                        docs={
                                          message?.documents &&
                                          message?.documents.length > 0
                                            ? message?.documents
                                            : parentMessage?.documents
                                        }
                                        setPresentingDocument={
                                          setPresentingDocument
                                        }
                                        continueGenerating={
                                          i == messageHistory.length - 1 &&
                                          currentCanContinue()
                                            ? continueGenerating
                                            : undefined
                                        }
                                        overriddenModel={
                                          message.overridden_model
                                        }
                                        regenerate={createRegenerator({
                                          messageId: message.messageId,
                                          parentMessage: parentMessage!,
                                        })}
                                        otherMessagesCanSwitchTo={
                                          parentMessage?.childrenMessageIds ||
                                          []
                                        }
                                        onMessageSelection={(messageId) => {
                                          const newCompleteMessageMap = new Map(
                                            messageMap
                                          );
                                          newCompleteMessageMap.get(
                                            message.parentMessageId!
                                          )!.latestChildMessageId = messageId;

                                          updateCompleteMessageDetail(
                                            currentSessionId(),
                                            newCompleteMessageMap
                                          );

                                          setSelectedMessageForDocDisplay(
                                            messageId
                                          );
                                          // set message as latest so we can edit this message
                                          // and so it sticks around on page reload
                                          setMessageAsLatest(messageId);
                                        }}
                                        isActive={
                                          messageHistory.length - 1 == i
                                        }
                                        selectedDocuments={selectedDocuments}
                                        toggleDocumentSelection={(
                                          second: boolean
                                        ) => {
                                          if (
                                            (!second &&
                                              !documentSidebarToggled) ||
                                            (documentSidebarToggled &&
                                              selectedMessageForDocDisplay ===
                                                message.messageId)
                                          ) {
                                            toggleDocumentSidebar();
                                          }
                                          if (
                                            (second &&
                                              !documentSidebarToggled) ||
                                            (documentSidebarToggled &&
                                              selectedMessageForDocDisplay ===
                                                secondLevelMessage?.messageId)
                                          ) {
                                            toggleDocumentSidebar();
                                          }

                                          setSelectedMessageForDocDisplay(
                                            second
                                              ? secondLevelMessage?.messageId ||
                                                  null
                                              : message.messageId
                                          );
                                        }}
                                        currentPersona={liveAssistant}
                                        alternativeAssistant={
                                          currentAlternativeAssistant
                                        }
                                        messageId={message.messageId}
                                        content={message.message}
                                        files={message.files}
                                        query={
                                          messageHistory[i]?.query || undefined
                                        }
                                        citedDocuments={getCitedDocumentsFromMessage(
                                          message
                                        )}
                                        toolCall={message.toolCall}
                                        isComplete={
                                          i !== messageHistory.length - 1 ||
                                          (currentSessionChatState !=
                                            "streaming" &&
                                            currentSessionChatState !=
                                              "toolBuilding")
                                        }
                                        handleFeedback={
                                          i === messageHistory.length - 1 &&
                                          currentSessionChatState != "input"
                                            ? undefined
                                            : (feedbackType: FeedbackType) =>
                                                setCurrentFeedback([
                                                  feedbackType,
                                                  message.messageId as number,
                                                ])
                                        }
                                      />
                                    ) : (
                                      <AIMessage
                                        docs={
                                          message?.documents &&
                                          message?.documents.length > 0
                                            ? message?.documents
                                            : parentMessage?.documents
                                        }
                                        setPresentingDocument={
                                          setPresentingDocument
                                        }
                                        index={i}
                                        continueGenerating={
                                          i == messageHistory.length - 1 &&
                                          currentCanContinue()
                                            ? continueGenerating
                                            : undefined
                                        }
                                        overriddenModel={
                                          message.overridden_model
                                        }
                                        regenerate={createRegenerator({
                                          messageId: message.messageId,
                                          parentMessage: parentMessage!,
                                        })}
                                        otherMessagesCanSwitchTo={
                                          parentMessage?.childrenMessageIds ||
                                          []
                                        }
                                        onMessageSelection={(messageId) => {
                                          const newCompleteMessageMap = new Map(
                                            messageMap
                                          );
                                          newCompleteMessageMap.get(
                                            message.parentMessageId!
                                          )!.latestChildMessageId = messageId;

                                          updateCompleteMessageDetail(
                                            currentSessionId(),
                                            newCompleteMessageMap
                                          );

                                          setSelectedMessageForDocDisplay(
                                            messageId
                                          );
                                          // set message as latest so we can edit this message
                                          // and so it sticks around on page reload
                                          setMessageAsLatest(messageId);
                                        }}
                                        isActive={
                                          messageHistory.length - 1 == i
                                        }
                                        selectedDocuments={selectedDocuments}
                                        toggleDocumentSelection={() => {
                                          if (
                                            !documentSidebarToggled ||
                                            (documentSidebarToggled &&
                                              selectedMessageForDocDisplay ===
                                                message.messageId)
                                          ) {
                                            toggleDocumentSidebar();
                                          }

                                          setSelectedMessageForDocDisplay(
                                            message.messageId
                                          );
                                        }}
                                        currentPersona={liveAssistant}
                                        alternativeAssistant={
                                          currentAlternativeAssistant
                                        }
                                        messageId={message.messageId}
                                        content={message.message}
                                        files={message.files}
                                        query={
                                          messageHistory[i]?.query || undefined
                                        }
                                        citedDocuments={getCitedDocumentsFromMessage(
                                          message
                                        )}
                                        toolCall={message.toolCall}
                                        isComplete={
                                          i !== messageHistory.length - 1 ||
                                          (currentSessionChatState !=
                                            "streaming" &&
                                            currentSessionChatState !=
                                              "toolBuilding")
                                        }
                                        hasDocs={
                                          (message.documents &&
                                            message.documents.length > 0) ===
                                          true
                                        }
                                        handleFeedback={
                                          i === messageHistory.length - 1 &&
                                          currentSessionChatState != "input"
                                            ? undefined
                                            : (feedbackType) =>
                                                setCurrentFeedback([
                                                  feedbackType,
                                                  message.messageId as number,
                                                ])
                                        }
                                        handleSearchQueryEdit={
                                          i === messageHistory.length - 1 &&
                                          currentSessionChatState == "input"
                                            ? (newQuery) => {
                                                if (!previousMessage) {
                                                  setPopup({
                                                    type: "error",
                                                    message:
                                                      "Cannot edit query of first message - please refresh the page and try again.",
                                                  });
                                                  return;
                                                }
                                                if (
                                                  previousMessage.messageId ===
                                                  null
                                                ) {
                                                  setPopup({
                                                    type: "error",
                                                    message:
                                                      "Cannot edit query of a pending message - please wait a few seconds and try again.",
                                                  });
                                                  return;
                                                }
                                                onSubmit({
                                                  messageIdToResend:
                                                    previousMessage.messageId,
                                                  queryOverride: newQuery,
                                                  alternativeAssistantOverride:
                                                    currentAlternativeAssistant,
                                                });
                                              }
                                            : undefined
                                        }
                                        handleForceSearch={() => {
                                          if (
                                            previousMessage &&
                                            previousMessage.messageId
                                          ) {
                                            createRegenerator({
                                              messageId: message.messageId,
                                              parentMessage: parentMessage!,
                                              forceSearch: true,
                                            })(llmOverrideManager.llmOverride);
                                          } else {
                                            setPopup({
                                              type: "error",
                                              message:
                                                "Failed to force search - please refresh the page and try again.",
                                            });
                                          }
                                        }}
                                        retrievalDisabled={
                                          currentAlternativeAssistant
                                            ? !personaIncludesRetrieval(
                                                currentAlternativeAssistant!
                                              )
                                            : !retrievalEnabled
                                        }
                                      />
                                    )}
                                  </div>
                                );
                              } else {
                                return (
                                  <div key={messageReactComponentKey}>
                                    <AIMessage
                                      setPresentingDocument={
                                        setPresentingDocument
                                      }
                                      currentPersona={liveAssistant}
                                      messageId={message.messageId}
                                      content={
                                        <p className="text-red-700 text-sm my-auto">
                                          {message.message}
                                          {message.stackTrace && (
                                            <span
                                              onClick={() =>
                                                setStackTraceModalContent(
                                                  message.stackTrace!
                                                )
                                              }
                                              className="ml-2 cursor-pointer underline"
                                            >
                                              Show stack trace.
                                            </span>
                                          )}
                                        </p>
                                      }
                                    />
                                  </div>
                                );
                              }
                            })}

                            {(currentSessionChatState == "loading" ||
                              (loadingError &&
                                !currentSessionRegenerationState?.regenerating &&
                                messageHistory[messageHistory.length - 1]
                                  ?.type != "user")) && (
                              <HumanMessage
                                key={-2}
                                messageId={-1}
                                content={submittedMessage}
                              />
                            )}

                            {currentSessionChatState == "loading" && (
                              <div
                                key={`${messageHistory.length}-${chatSessionIdRef.current}`}
                              >
                                <AIMessage
                                  setPresentingDocument={setPresentingDocument}
                                  key={-3}
                                  currentPersona={liveAssistant}
                                  alternativeAssistant={
                                    alternativeGeneratingAssistant ??
                                    alternativeAssistant
                                  }
                                  messageId={null}
                                  content={
                                    <div
                                      key={"Generating"}
                                      className="mr-auto relative inline-block"
                                    >
                                      <span className="text-sm loading-text">
                                        Thinking...
                                      </span>
                                    </div>
                                  }
                                />
                              </div>
                            )}

                            {loadingError && (
                              <div key={-1}>
                                <AIMessage
                                  setPresentingDocument={setPresentingDocument}
                                  currentPersona={liveAssistant}
                                  messageId={-1}
                                  content={
                                    <p className="text-red-700 text-sm my-auto">
                                      {loadingError}
                                    </p>
                                  }
                                />
                              </div>
                            )}
                            {messageHistory.length > 0 && (
                              <div
                                style={{
                                  height: !autoScrollEnabled
                                    ? getContainerHeight()
                                    : undefined,
                                }}
                              />
                            )}

                            {/* Some padding at the bottom so the search bar has space at the bottom to not cover the last message*/}
                            <div ref={endPaddingRef} className="h-[95px]" />

                            <div ref={endDivRef} />
                          </div>
                        </div>
                        <div
                          ref={inputRef}
                          className="absolute pointer-events-none bottom-0 z-10 w-full"
                        >
                          {aboveHorizon && (
                            <div className="mx-auto w-fit !pointer-events-none flex sticky justify-center">
                              <button
                                onClick={() => clientScrollToBottom()}
                                className="p-1 pointer-events-auto rounded-2xl bg-background-strong border border-border  mx-auto "
                              >
                                <FiArrowDown size={18} />
                              </button>
                            </div>
                          )}
                          <div className="pointer-events-auto w-[95%] mx-auto relative mb-8">
                            <ChatInputBar
                              proSearchEnabled={proSearchEnabled}
                              setProSearchEnabled={() => toggleProSearch()}
                              toggleDocumentSidebar={toggleDocumentSidebar}
                              availableSources={sources}
                              availableDocumentSets={documentSets}
                              availableTags={tags}
                              filterManager={filterManager}
                              llmOverrideManager={llmOverrideManager}
                              removeDocs={() => {
                                clearSelectedDocuments();
                              }}
                              retrievalEnabled={retrievalEnabled}
                              showConfigureAPIKey={() =>
                                setShowApiKeyModal(true)
                              }
                              chatState={currentSessionChatState}
                              stopGenerating={stopGenerating}
                              selectedDocuments={selectedDocuments}
                              // assistant stuff
                              selectedAssistant={liveAssistant}
                              setAlternativeAssistant={setAlternativeAssistant}
                              alternativeAssistant={alternativeAssistant}
                              // end assistant stuff
                              message={message}
                              setMessage={setMessage}
                              onSubmit={onSubmit}
                              files={currentMessageFiles}
                              setFiles={setCurrentMessageFiles}
                              handleFileUpload={handleImageUpload}
                              textAreaRef={textAreaRef}
                            />
                            {enterpriseSettings &&
                              enterpriseSettings.custom_lower_disclaimer_content && (
                                <div className="mobile:hidden mt-4 flex items-center justify-center relative w-[95%] mx-auto">
                                  <div className="text-sm text-text-500 max-w-searchbar-max px-4 text-center">
                                    <MinimalMarkdown
                                      content={
                                        enterpriseSettings.custom_lower_disclaimer_content
                                      }
                                    />
                                  </div>
                                </div>
                              )}
                            {enterpriseSettings &&
                              enterpriseSettings.use_custom_logotype && (
                                <div className="hidden lg:block absolute right-0 bottom-0">
                                  <img
                                    src="/api/enterprise-settings/logotype"
                                    alt="logotype"
                                    style={{ objectFit: "contain" }}
                                    className="w-fit h-8"
                                  />
                                </div>
                              )}
                          </div>
                        </div>
                      </div>

                      <div
                        style={{ transition: "width 0.30s ease-out" }}
                        className={`
                          flex-none 
                          overflow-y-hidden 
                          transition-all 
                          bg-opacity-80
                          duration-300 
                          ease-in-out
                          h-full
                          ${
                            documentSidebarToggled && !settings?.isMobile
                              ? "w-[350px]"
                              : "w-[0px]"
                          }
                      `}
                      ></div>
                    </div>
                  )}
                </Dropzone>
              ) : (
                <div className="mx-auto h-full flex">
                  <div
                    style={{ transition: "width 0.30s ease-out" }}
                    className={`flex-none bg-transparent transition-all bg-opacity-80 duration-300 ease-in-out h-full
                        ${
                          toggledSidebar && !settings?.isMobile
                            ? "w-[250px] "
                            : "w-[0px]"
                        }`}
                  />
                  <div className="my-auto">
                    <OnyxInitializingLoader />
                  </div>
                </div>
              )}
            </div>
          </div>
          <FixedLogo backgroundToggled={toggledSidebar || showHistorySidebar} />
        </div>
        {/* Right Sidebar - DocumentSidebar */}
      </div>
    </>
  );
}
