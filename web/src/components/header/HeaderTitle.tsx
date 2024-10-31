"use client";

import React from "react";

export function HeaderTitle({
  children,
  backgroundToggled,
}: {
  children: JSX.Element | string;
  backgroundToggled?: boolean;
}) {
  const isString = typeof children === "string";
  const textSize = isString && children.length > 10 ? "text-xl" : "text-2xl";

  return (
    <h1
      className={`${textSize} ${
        backgroundToggled
          ? "text-text-sidebar-toggled-header"
          : "text-text-sidebar-header"
      } break-words line-clamp-2 ellipsis text-strong leading-none font-bold`}
    >
      {children}
    </h1>
  );
}
