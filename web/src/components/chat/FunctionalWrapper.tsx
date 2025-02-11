"use client";

import React, { ReactNode, useState } from "react";

export default function FunctionalWrapper({
  initiallyToggled,
  content,
}: {
  content: (
    toggledSidebar: boolean,
    toggle: (toggled?: boolean) => void
  ) => ReactNode;
  initiallyToggled: boolean;
}) {
  const [toggledSidebar, setToggledSidebar] = useState(initiallyToggled);

  const toggle = (value?: boolean) => {
    setToggledSidebar((toggledSidebar) =>
      value !== undefined ? value : !toggledSidebar
    );
  };

  return (
    <>
      <div className="overscroll-y-contain overflow-y-scroll overscroll-contain left-0 top-0 w-full h-svh">
        {content(toggledSidebar, toggle)}
      </div>
    </>
  );
}
