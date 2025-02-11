"use client";
import Prism from "prismjs";

import { humanReadableFormat } from "@/lib/time";
import { BackendChatSession } from "../../interfaces";
import {
  buildLatestMessageChain,
  getCitedDocumentsFromMessage,
  processRawChatHistory,
} from "../../lib";
import { AIMessage, HumanMessage } from "../../message/Messages";
import { AgenticMessage } from "../../message/AgenticMessage";
import { Callout } from "@/components/ui/callout";
import { useContext, useEffect, useState } from "react";
import { SettingsContext } from "@/components/settings/SettingsProvider";
import { OnyxInitializingLoader } from "@/components/OnyxInitializingLoader";
import { Persona } from "@/app/admin/assistants/interfaces";
import { Button } from "@/components/ui/button";
import { OnyxDocument } from "@/lib/search/interfaces";
import TextView from "@/components/chat/TextView";
import { DocumentResults } from "../../documentSidebar/DocumentResults";
import { Modal } from "@/components/Modal";
import FunctionalHeader from "@/components/chat/Header";
import FixedLogo from "../../../../components/logo/FixedLogo";
import { useRouter } from "next/navigation";

function BackToOnyxButton({
  documentSidebarToggled,
}: {
  documentSidebarToggled: boolean;
}) {
  const router = useRouter();
  const enterpriseSettings = useContext(SettingsContext)?.enterpriseSettings;

  return (
    <div className="absolute bottom-0 bg-background w-full flex border-t border-border py-4">
      <div className="mx-auto">
        <Button onClick={() => router.push("/chat")}>
          Back to {enterpriseSettings?.application_name || "Onyx Chat"}
        </Button>
      </div>
      <div
        style={{ transition: "width 0.30s ease-out" }}
        className={`
            flex-none 
            overflow-y-hidden 
            transition-all 
            duration-300 
            ease-in-out
            ${documentSidebarToggled ? "w-[400px]" : "w-[0px]"}
        `}
      ></div>
    </div>
  );
}

export function SharedChatDisplay({
  chatSession,
  persona,
}: {
  chatSession: BackendChatSession | null;
  persona: Persona;
}) {
  const settings = useContext(SettingsContext);
  const [documentSidebarToggled, setDocumentSidebarToggled] = useState(false);
  const [selectedMessageForDocDisplay, setSelectedMessageForDocDisplay] =
    useState<number | null>(null);
  const [isReady, setIsReady] = useState(false);
  const [presentingDocument, setPresentingDocument] =
    useState<OnyxDocument | null>(null);

  const toggleDocumentSidebar = () => {
    setDocumentSidebarToggled(!documentSidebarToggled);
  };

  useEffect(() => {
    Prism.highlightAll();
    setIsReady(true);
  }, []);
  if (!chatSession) {
    return (
      <div className="min-h-full w-full">
        <div className="mx-auto w-fit pt-8">
          <Callout type="danger" title="Shared Chat Not Found">
            Did not find a shared chat with the specified ID.
          </Callout>
        </div>
        <BackToOnyxButton documentSidebarToggled={documentSidebarToggled} />
      </div>
    );
  }

  const messages = buildLatestMessageChain(
    processRawChatHistory(chatSession.messages)
  );

  return (
    <>
      {presentingDocument && (
        <TextView
          presentingDocument={presentingDocument}
          onClose={() => setPresentingDocument(null)}
        />
      )}
      {documentSidebarToggled && settings?.isMobile && (
        <div className="md:hidden">
          <Modal noPadding noScroll>
            <DocumentResults
              agenticMessage={false}
              isSharedChat={true}
              selectedMessage={
                selectedMessageForDocDisplay
                  ? messages.find(
                      (message) =>
                        message.messageId === selectedMessageForDocDisplay
                    ) || null
                  : null
              }
              toggleDocumentSelection={() => {
                setDocumentSidebarToggled(true);
              }}
              selectedDocuments={[]}
              clearSelectedDocuments={() => {}}
              selectedDocumentTokens={0}
              maxTokens={0}
              initialWidth={400}
              isOpen={true}
              setPresentingDocument={setPresentingDocument}
              modal={true}
              closeSidebar={() => {
                setDocumentSidebarToggled(false);
              }}
            />
          </Modal>
        </div>
      )}

      <div className="fixed inset-0 flex flex-col text-default">
        <div className="h-[100dvh] px-2 overflow-y-hidden">
          <div className="w-full h-[100dvh] flex flex-col overflow-hidden">
            {!settings?.isMobile && (
              <div
                style={{ transition: "width 0.30s ease-out" }}
                className={`
                  flex-none 
                  fixed
                  right-0
                  z-[1000]
                  bg-background
                  h-screen
                  transition-all
                  bg-opacity-80
                  duration-300
                  ease-in-out
                  bg-transparent
                  transition-all
                  bg-opacity-80
                  duration-300
                  ease-in-out
                  h-full
                  ${documentSidebarToggled ? "w-[400px]" : "w-[0px]"}
            `}
              >
                <DocumentResults
                  agenticMessage={false}
                  modal={false}
                  isSharedChat={true}
                  selectedMessage={
                    selectedMessageForDocDisplay
                      ? messages.find(
                          (message) =>
                            message.messageId === selectedMessageForDocDisplay
                        ) || null
                      : null
                  }
                  toggleDocumentSelection={() => {
                    setDocumentSidebarToggled(true);
                  }}
                  clearSelectedDocuments={() => {}}
                  selectedDocumentTokens={0}
                  maxTokens={0}
                  initialWidth={400}
                  isOpen={true}
                  setPresentingDocument={setPresentingDocument}
                  closeSidebar={() => {
                    setDocumentSidebarToggled(false);
                  }}
                  selectedDocuments={[]}
                />
              </div>
            )}
            <div className="flex mobile:hidden max-h-full overflow-hidden ">
              <FunctionalHeader
                sidebarToggled={false}
                toggleSidebar={() => {}}
                page="chat"
                reset={() => {}}
              />
            </div>

            <div className="flex w-full overflow-hidden overflow-y-scroll">
              <div className="w-full h-full   flex-col flex max-w-message-max mx-auto">
                <div className="fixed z-10 w-full ">
                  <div className="bg-background relative px-5 pt-4 w-full">
                    <h1 className="text-3xl text-strong font-bold">
                      {chatSession.description || `Unnamed Chat`}
                    </h1>
                    <p className=" text-text-darker">
                      {humanReadableFormat(chatSession.time_created)}
                    </p>
                    <div
                      className={`
                      h-full absolute top-0  z-10 w-full sm:w-[90%] lg:w-[70%]
                      bg-gradient-to-b via-50% z-[-1] from-background via-background to-background/10 flex
                      transition-all duration-300 ease-in-out
                      ${
                        documentSidebarToggled
                          ? "left-[200px] transform -translate-x-[calc(50%+100px)]"
                          : "left-1/2 transform -translate-x-1/2"
                      }
                    `}
                    />
                  </div>
                </div>
                {isReady ? (
                  <div className="w-full pt-24 pb-16">
                    {messages.map((message, i) => {
                      if (message.type === "user") {
                        return (
                          <HumanMessage
                            shared
                            key={message.messageId}
                            content={message.message}
                            files={message.files}
                          />
                        );
                      } else if (message.type === "assistant") {
                        const secondLevelMessage =
                          messages[messages.indexOf(message) + 1]?.type ===
                          "assistant"
                            ? messages[messages.indexOf(message) + 1]
                            : undefined;

                        const secondLevelAssistantMessage =
                          messages[messages.indexOf(message) + 1]?.type ===
                          "assistant"
                            ? messages[messages.indexOf(message) + 1]?.message
                            : undefined;

                        const agenticDocs =
                          messages[messages.indexOf(message) + 1]?.type ===
                          "assistant"
                            ? messages[messages.indexOf(message) + 1]?.documents
                            : undefined;

                        if (messages[i - 1]?.type === "assistant") {
                          return null;
                        }

                        if (
                          message.sub_questions &&
                          message.sub_questions.length > 0
                        ) {
                          return (
                            <AgenticMessage
                              shared
                              key={message.messageId}
                              isImprovement={message.isImprovement}
                              secondLevelGenerating={false}
                              secondLevelSubquestions={message.sub_questions?.filter(
                                (subQuestion) => subQuestion.level === 1
                              )}
                              secondLevelAssistantMessage={
                                (message.second_level_message &&
                                message.second_level_message.length > 0
                                  ? message.second_level_message
                                  : secondLevelAssistantMessage) || undefined
                              }
                              subQuestions={
                                message.sub_questions?.filter(
                                  (subQuestion) => subQuestion.level === 0
                                ) || []
                              }
                              agenticDocs={message.agentic_docs || agenticDocs}
                              toggleDocDisplay={(agentic: boolean) => {
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
                              docs={message?.documents}
                              setPresentingDocument={setPresentingDocument}
                              overriddenModel={message.overridden_model}
                              currentPersona={persona}
                              messageId={message.messageId}
                              content={message.message}
                              files={message.files}
                              query={message.query || undefined}
                              citedDocuments={getCitedDocumentsFromMessage(
                                message
                              )}
                              toolCall={message.toolCall}
                              isComplete={true}
                              selectedDocuments={[]}
                              toggleDocumentSelection={() => {
                                if (
                                  !documentSidebarToggled ||
                                  (documentSidebarToggled &&
                                    selectedMessageForDocDisplay ===
                                      message.messageId)
                                ) {
                                  setDocumentSidebarToggled(
                                    !documentSidebarToggled
                                  );
                                }
                                setSelectedMessageForDocDisplay(
                                  message.messageId
                                );
                              }}
                            />
                          );
                        } else {
                          return (
                            <AIMessage
                              shared
                              key={message.messageId}
                              docs={message?.documents}
                              setPresentingDocument={setPresentingDocument}
                              overriddenModel={message.overridden_model}
                              currentPersona={persona}
                              messageId={message.messageId}
                              content={message.message}
                              files={message.files}
                              query={message.query || undefined}
                              citedDocuments={getCitedDocumentsFromMessage(
                                message
                              )}
                              toolCall={message.toolCall}
                              isComplete={true}
                              hasDocs={
                                (message.documents &&
                                  message.documents.length > 0) === true
                              }
                              selectedDocuments={[]}
                              toggleDocumentSelection={() => {
                                if (
                                  !documentSidebarToggled ||
                                  (documentSidebarToggled &&
                                    selectedMessageForDocDisplay ===
                                      message.messageId)
                                ) {
                                  setDocumentSidebarToggled(
                                    !documentSidebarToggled
                                  );
                                }
                                setSelectedMessageForDocDisplay(
                                  message.messageId
                                );
                              }}
                              retrievalDisabled={false}
                            />
                          );
                        }
                      } else {
                        return (
                          <div key={message.messageId}>
                            <AgenticMessage
                              shared
                              subQuestions={message.sub_questions || []}
                              currentPersona={persona}
                              messageId={message.messageId}
                              content={
                                <p className="text-red-700 text-sm my-auto">
                                  {message.message}
                                </p>
                              }
                            />
                          </div>
                        );
                      }
                    })}
                  </div>
                ) : (
                  <div className="grow flex-0 h-screen w-full flex items-center justify-center">
                    <div className="mb-[33vh]">
                      <OnyxInitializingLoader />
                    </div>
                  </div>
                )}
              </div>
              {!settings?.isMobile && (
                <div
                  style={{ transition: "width 0.30s ease-out" }}
                  className={`
                          flex-none 
                          overflow-y-hidden 
                          transition-all 
                          duration-300 
                          ease-in-out
                          ${documentSidebarToggled ? "w-[400px]" : "w-[0px]"}
                      `}
                ></div>
              )}
            </div>
          </div>

          <FixedLogo backgroundToggled={false} />
          <BackToOnyxButton documentSidebarToggled={documentSidebarToggled} />
        </div>
      </div>
    </>
  );
}
