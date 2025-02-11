"use client";

import { ValidSources } from "@/lib/types";
import { SourceIcon } from "./SourceIcon";
import { useState } from "react";
import { OnyxIcon } from "./icons/icons";

export function WebResultIcon({
  url,
  size = 18,
}: {
  url: string;
  size?: number;
}) {
  const [error, setError] = useState(false);
  let hostname;
  try {
    hostname = new URL(url).hostname;
  } catch (e) {
    // console.log(e);
    hostname = "docs.onyx.app";
  }
  return (
    <>
      {hostname == "docs.onyx.app" ? (
        <OnyxIcon size={size} />
      ) : !error ? (
        <img
          className="my-0 rounded-full py-0"
          src={`https://t3.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=https://${hostname}&size=128`}
          alt="favicon"
          height={size}
          onError={() => setError(true)}
          width={size}
          style={{
            height: `${size}px`,
            width: `${size}px`,
            background: "transparent",
          }}
        />
      ) : (
        <SourceIcon sourceType={ValidSources.Web} iconSize={size} />
      )}
    </>
  );
}
