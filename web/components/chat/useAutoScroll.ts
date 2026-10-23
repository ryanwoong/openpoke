import { useCallback, useEffect, useRef } from "react";

interface AutoScrollOptions {
  items: ReadonlyArray<unknown>;
  isWaiting: boolean;
  isVisible?: boolean;
}

export const useAutoScroll = ({ items, isWaiting, isVisible = true }: AutoScrollOptions) => {
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const isUserNearBottomRef = useRef(true);
  const wasVisibleRef = useRef(isVisible);
  const wasWaitingRef = useRef(isWaiting);
  const previousItemCountRef = useRef(0);
  const scrollTargetRef = useRef<number | null>(null);

  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container || !container.clientHeight) return;

    if (scrollTargetRef.current !== null) {
      if (Math.abs(container.scrollTop - scrollTargetRef.current) > 2) return;
      scrollTargetRef.current = null;
    }
    const threshold = 80;
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    isUserNearBottomRef.current = distanceFromBottom <= threshold;
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const target = Math.max(0, container.scrollHeight - container.clientHeight);
    scrollTargetRef.current = target;
    container.scrollTo({ top: target, behavior: reducedMotion ? "auto" : behavior });
  }, []);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const interrupt = () => {
      scrollTargetRef.current = null;
    };
    container.addEventListener("wheel", interrupt, { passive: true });
    container.addEventListener("touchstart", interrupt, { passive: true });
    container.addEventListener("pointerdown", interrupt);
    container.addEventListener("keydown", interrupt);
    return () => {
      container.removeEventListener("wheel", interrupt);
      container.removeEventListener("touchstart", interrupt);
      container.removeEventListener("pointerdown", interrupt);
      container.removeEventListener("keydown", interrupt);
    };
  }, []);

  useEffect(() => {
    const startedWaiting = isWaiting && !wasWaitingRef.current;
    wasWaitingRef.current = isWaiting;
    if (startedWaiting) isUserNearBottomRef.current = true;
    if (!isVisible) {
      wasVisibleRef.current = false;
      return;
    }
    if (isUserNearBottomRef.current) {
      const behavior = !wasVisibleRef.current || !previousItemCountRef.current || startedWaiting ? "auto" : "smooth";
      scrollToBottom(behavior);
    }
    wasVisibleRef.current = true;
    previousItemCountRef.current = items.length;
  }, [items, isWaiting, isVisible, scrollToBottom]);

  return {
    scrollContainerRef,
    handleScroll,
    scrollToBottom,
  };
};
