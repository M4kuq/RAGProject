import type { SVGProps } from "react";

export type AdminIconName =
  | "check"
  | "dashboard"
  | "debug"
  | "documents"
  | "evaluations"
  | "jobs"
  | "review"
  | "search"
  | "spark";

type AdminIconProps = SVGProps<SVGSVGElement> & {
  name: AdminIconName;
};

export function AdminIcon({ name, ...props }: AdminIconProps) {
  return (
    <svg aria-hidden="true" fill="none" viewBox="0 0 24 24" {...props}>
      {iconPath(name)}
    </svg>
  );
}

function iconPath(name: AdminIconName) {
  switch (name) {
    case "check":
      return <path d="m5 12 4 4L19 6" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />;
    case "dashboard":
      return (
        <path d="M4 13h7V4H4v9ZM13 20h7v-9h-7v9ZM4 20h7v-5H4v5ZM13 9h7V4h-7v5Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.8" />
      );
    case "debug":
      return (
        <>
          <path d="M8 7a4 4 0 0 1 8 0v1H8V7ZM6 12h12M7 8h10v5a5 5 0 0 1-10 0V8Z" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
          <path d="m4 10 2 1M20 10l-2 1M4 17l2-1M20 17l-2-1" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
        </>
      );
    case "documents":
      return (
        <>
          <path d="M7 3h7l4 4v14H7V3Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.8" />
          <path d="M14 3v5h5M10 12h6M10 16h6" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
        </>
      );
    case "evaluations":
      return (
        <>
          <path d="M5 19V9M12 19V5M19 19v-7" stroke="currentColor" strokeLinecap="round" strokeWidth="2" />
          <path d="M4 19h16" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
        </>
      );
    case "jobs":
      return (
        <>
          <path d="M6 7h12v12H6V7Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.8" />
          <path d="M9 3v4M15 3v4M9 11h6M9 15h4" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
        </>
      );
    case "review":
      return (
        <>
          <path d="M6 4h9l3 3v13H6V4Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.8" />
          <path d="M14 4v4h4M9 14l2 2 4-5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
        </>
      );
    case "search":
      return (
        <>
          <circle cx="11" cy="11" r="6" stroke="currentColor" strokeWidth="1.9" />
          <path d="m16 16 4 4" stroke="currentColor" strokeLinecap="round" strokeWidth="1.9" />
        </>
      );
    case "spark":
      return (
        <path d="M12 3 9.8 8.8 4 11l5.8 2.2L12 19l2.2-5.8L20 11l-5.8-2.2L12 3Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.8" />
      );
  }
}
