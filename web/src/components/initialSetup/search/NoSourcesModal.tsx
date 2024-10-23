"use client";

import { Button, Divider, Text } from "@tremor/react";
import { Modal } from "../../Modal";
import Link from "next/link";
import { FiMessageSquare, FiShare2 } from "react-icons/fi";
import { useContext, useState } from "react";
import { SettingsContext } from "@/components/settings/SettingsProvider";

export function NoSourcesModal() {
  const settings = useContext(SettingsContext);
  const [isHidden, setIsHidden] = useState(
    !settings?.settings.search_page_enabled
  );

  if (isHidden) {
    return null;
  }

  return (
    <Modal
      width="max-w-3xl w-full"
      title="🧐 No sources connected"
      onOutsideClick={() => setIsHidden(true)}
    >
      <div className="text-base">
        <div>
          <Text>
            Before using Search you&apos;ll need to connect at least one source.
            Without any connected knowledge sources, there isn&apos;t anything
            to search over.
          </Text>
          <Link href="/admin/add-connector">
            <Button className="mt-3" size="xs" icon={FiShare2}>
              Connect a Source!
            </Button>
          </Link>
          <Divider />
          <div>
            <Text>
              Or, if you&apos;re looking for a pure ChatGPT-like experience
              without any organization specific knowledge, then you can head
              over to the Chat page and start chatting with Nanswer right away!
            </Text>
            <Link href="/chat">
              <Button className="mt-3" size="xs" icon={FiMessageSquare}>
                Start Chatting!
              </Button>
            </Link>
          </div>
        </div>
      </div>
    </Modal>
  );
}
