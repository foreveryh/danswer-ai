import { ReactNode } from "react";
import { CompactDocumentCard, CompactQuestionCard } from "../DocumentDisplay";
import { LoadedOnyxDocument, OnyxDocument } from "@/lib/search/interfaces";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { openDocument } from "@/lib/search/utils";
import { SubQuestionDetail } from "@/app/chat/interfaces";

export interface DocumentCardProps {
  document: LoadedOnyxDocument;
  updatePresentingDocument: (document: OnyxDocument) => void;
  icon?: React.ReactNode;
  url?: string;
}
export interface QuestionCardProps {
  question: SubQuestionDetail;
  openQuestion: (question: SubQuestionDetail) => void;
}

export function Citation({
  children,
  document_info,
  question_info,
  index,
}: {
  document_info?: DocumentCardProps;
  question_info?: QuestionCardProps;
  children?: JSX.Element | string | null | ReactNode;
  index?: number;
}) {
  const innerText = children
    ? children?.toString().split("[")[1].split("]")[0]
    : index;

  if (!document_info && !question_info) {
    return <>{children}</>;
  }
  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            onClick={() => {
              document_info?.document
                ? openDocument(
                    document_info.document,
                    document_info.updatePresentingDocument
                  )
                : question_info?.question
                  ? question_info.openQuestion(question_info.question)
                  : null;
            }}
            className="inline-flex items-center cursor-pointer transition-all duration-200 ease-in-out"
          >
            <span
              className="flex items-center justify-center  px-1 h-4 text-[10px] font-medium text-text-700 bg-background-100 rounded-full border border-background-300 hover:bg-background-200 hover:text-text-900 shadow-sm"
              style={{ transform: "translateY(-10%)", lineHeight: "1" }}
            >
              {innerText}
            </span>
          </span>
        </TooltipTrigger>
        <TooltipContent
          className="dark:border dark:!bg-[#000] border-neutral-700"
          width="mb-2 max-w-lg"
        >
          {document_info?.document ? (
            <CompactDocumentCard
              updatePresentingDocument={document_info.updatePresentingDocument}
              url={document_info.url}
              icon={document_info.icon}
              document={document_info.document}
            />
          ) : (
            <CompactQuestionCard
              question={question_info?.question!}
              openQuestion={question_info?.openQuestion!}
            />
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
