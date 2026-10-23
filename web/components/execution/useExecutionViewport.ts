"use client";

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

type Camera = { x: number; y: number; zoom: number };
type Bounds = { x: number; y: number; width: number; height: number };

export function useExecutionViewport(bounds: Bounds, resetKey: number) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [camera, setCamera] = useState<Camera | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const gesture = useRef<{ id: number; x: number; y: number; camera: Camera; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const zoom = Math.max(0.001, Math.min(1, Math.max(1, size.width - 64) / bounds.width, Math.max(1, size.height - 64) / bounds.height));
  const fitted = { x: size.width / 2 - (bounds.x + bounds.width / 2) * zoom, y: size.height / 2 - (bounds.y + bounds.height / 2) * zoom, zoom };
  const view = camera ?? fitted;
  const currentView = useRef(view);
  currentView.current = view;

  useEffect(() => setCamera(null), [resetKey]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width && height) setSize({ width, height });
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  function zoomAt(nextZoom: number, point = { x: size.width / 2, y: size.height / 2 }) {
    const previous = currentView.current;
    const zoom = Math.max(0.001, Math.min(1.5, nextZoom));
    const ratio = zoom / previous.zoom;
    const next = { x: point.x - (point.x - previous.x) * ratio, y: point.y - (point.y - previous.y) * ratio, zoom };
    currentView.current = next;
    setCamera(next);
  }

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    function wheel(event: WheelEvent) {
      event.preventDefault();
      const previous = currentView.current;
      if (!event.deltaY) return;
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport!.clientHeight : 1;
      const rect = viewport!.getBoundingClientRect();
      const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      const zoom = Math.max(0.001, Math.min(1.5, previous.zoom * Math.exp(-event.deltaY * unit * 0.002)));
      const ratio = zoom / previous.zoom;
      const next = { x: point.x - (point.x - previous.x) * ratio, y: point.y - (point.y - previous.y) * ratio, zoom };
      currentView.current = next;
      setCamera(next);
    }
    viewport.addEventListener("wheel", wheel, { passive: false });
    return () => viewport.removeEventListener("wheel", wheel);
  }, []);

  function endDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (gesture.current?.id !== event.pointerId) return;
    suppressClick.current = gesture.current.moved;
    gesture.current = null;
    setIsDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }

  return {
    viewportRef,
    view,
    isDragging,
    fit: () => setCamera(null),
    zoomTo: (zoom: number) => zoomAt(zoom),
    handlers: {
      onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => {
        if (event.button !== 0 || !event.isPrimary || gesture.current) return;
        suppressClick.current = false;
        gesture.current = { id: event.pointerId, x: event.clientX, y: event.clientY, camera: currentView.current, moved: false };
      },
      onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => {
        const start = gesture.current;
        if (!start || start.id !== event.pointerId) return;
        const dx = event.clientX - start.x;
        const dy = event.clientY - start.y;
        if (!start.moved && Math.hypot(dx, dy) < 4) return;
        start.moved = true;
        event.currentTarget.setPointerCapture(event.pointerId);
        setIsDragging(true);
        const next = { ...start.camera, x: start.camera.x + dx, y: start.camera.y + dy };
        currentView.current = next;
        setCamera(next);
      },
      onPointerUp: endDrag,
      onPointerCancel: endDrag,
      onLostPointerCapture: endDrag,
      onClickCapture: (event: React.MouseEvent<HTMLDivElement>) => {
        if (!suppressClick.current || event.detail === 0) return;
        suppressClick.current = false;
        event.preventDefault();
        event.stopPropagation();
      },
      onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => {
        if (event.target !== event.currentTarget) return;
        const steps: Record<string, [number, number]> = { ArrowLeft: [60, 0], ArrowRight: [-60, 0], ArrowUp: [0, 60], ArrowDown: [0, -60] };
        const step = steps[event.key];
        if (!step) return;
        event.preventDefault();
        const previous = currentView.current;
        const next = { ...previous, x: previous.x + step[0], y: previous.y + step[1] };
        currentView.current = next;
        setCamera(next);
      },
    },
  };
}
