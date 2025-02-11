"use client";

import { ErrorCallout } from "@/components/ErrorCallout";
import { FiPlusSquare } from "react-icons/fi";
import { ThreeDotsLoader } from "@/components/Loading";
import { InstantSSRAutoRefresh } from "@/components/SSRAutoRefresh";
import { AdminPageTitle } from "@/components/admin/Title";
import { Button } from "@/components/ui/button";
import Link from "next/link";
import { SourceIcon } from "@/components/SourceIcon";
import { SlackBotTable } from "./SlackBotTable";
import { useSlackBots } from "./[bot-id]/hooks";
import { ValidSources } from "@/lib/types";
import CreateButton from "@/components/ui/createButton";

const Main = () => {
  const {
    data: slackBots,
    isLoading: isSlackBotsLoading,
    error: slackBotsError,
  } = useSlackBots();

  if (isSlackBotsLoading) {
    return <ThreeDotsLoader />;
  }

  if (slackBotsError || !slackBots) {
    const errorMsg =
      slackBotsError?.info?.message ||
      slackBotsError?.info?.detail ||
      "An unknown error occurred";

    return (
      <ErrorCallout errorTitle="Error loading apps" errorMsg={`${errorMsg}`} />
    );
  }

  return (
    <div className="mb-8">
      {/* {popup} */}

      <p className="mb-2 text-sm text-muted-foreground">
        Setup Slack bots that connect to Onyx. Once setup, you will be able to
        ask questions to Onyx directly from Slack. Additionally, you can:
      </p>

      <div className="mb-2">
        <ul className="list-disc mt-2 ml-4 text-sm text-muted-foreground">
          <li>
            Setup OnyxBot to automatically answer questions in certain channels.
          </li>
          <li>
            Choose which document sets OnyxBot should answer from, depending on
            the channel the question is being asked.
          </li>
          <li>
            Directly message OnyxBot to search just as you would in the web UI.
          </li>
        </ul>
      </div>

      <p className="mb-6 text-sm text-muted-foreground">
        Follow the{" "}
        <a
          className="text-blue-500 hover:underline"
          href="https://docs.onyx.app/slack_bot_setup"
          target="_blank"
          rel="noopener noreferrer"
        >
          guide{" "}
        </a>
        found in the Onyx documentation to get started!
      </p>

      <CreateButton href="/admin/bots/new" text="New Slack Bot" />

      <SlackBotTable slackBots={slackBots} />
    </div>
  );
};

const Page = () => {
  return (
    <div className="container mx-auto">
      <AdminPageTitle
        icon={<SourceIcon iconSize={36} sourceType={ValidSources.Slack} />}
        title="Slack Bots"
      />
      <InstantSSRAutoRefresh />

      <Main />
    </div>
  );
};

export default Page;
