"use client";

import { useEffect, useRef, type FormEvent, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react';

const MUTATING_CONTROL_SELECTOR = [
  'button',
  'input',
  'select',
  'textarea',
  '[role="button"]',
  '[contenteditable="true"]',
].join(',');

function isMutatingControl(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest(MUTATING_CONTROL_SELECTOR));
}

export default function LegacyReadOnlySurface({ children }: { children: ReactNode }) {
  const surfaceRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;

    const disableNativeControls = () => {
      surface.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
        'button, input, select, textarea',
      ).forEach(control => {
        control.disabled = true;
        control.setAttribute('aria-disabled', 'true');
      });
      surface.querySelectorAll<HTMLElement>('[role="button"], [contenteditable="true"]').forEach(control => {
        control.setAttribute('aria-disabled', 'true');
        control.tabIndex = -1;
      });
    };

    disableNativeControls();
    const observer = new MutationObserver(disableNativeControls);
    observer.observe(surface, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  const blockClick = (event: MouseEvent<HTMLDivElement>) => {
    if (!isMutatingControl(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
  };

  const blockKeyboardAction = (event: KeyboardEvent<HTMLDivElement>) => {
    if ((event.key !== 'Enter' && event.key !== ' ') || !isMutatingControl(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
  };

  const blockSubmit = (event: FormEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <div
      ref={surfaceRef}
      data-legacy-readonly="true"
      aria-describedby="legacy-readonly-description"
      onClickCapture={blockClick}
      onKeyDownCapture={blockKeyboardAction}
      onSubmitCapture={blockSubmit}
      className="[&_button:disabled]:cursor-not-allowed [&_button:disabled]:opacity-55 [&_input:disabled]:cursor-not-allowed [&_input:disabled]:opacity-65 [&_select:disabled]:cursor-not-allowed [&_select:disabled]:opacity-65 [&_textarea:disabled]:cursor-not-allowed [&_textarea:disabled]:opacity-65"
    >
      <span id="legacy-readonly-description" className="sr-only">
        此区域仅用于迁移对账，所有表单和写操作均已禁用。
      </span>
      {children}
    </div>
  );
}
