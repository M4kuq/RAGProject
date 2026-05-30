import { Link, useLocation } from "react-router-dom";

type AdminNavLink = {
  active?: (pathname: string) => boolean;
  label: string;
  to: string;
};

const links: AdminNavLink[] = [
  {
    to: "/admin/documents",
    label: "Documents",
    active: (pathname: string) =>
      pathname === "/admin/documents" ||
      (pathname.startsWith("/admin/documents/") && !pathname.startsWith("/admin/documents/review"))
  },
  {
    to: "/admin/documents/review",
    label: "Review",
    active: (pathname: string) => pathname === "/admin/documents/review"
  },
  { to: "/admin/evaluations", label: "Evaluations" },
  { to: "/admin/retrieval-debug", label: "Retrieval Debug" },
  { to: "/admin/jobs", label: "Jobs" }
];

export function AdminSidebar() {
  const location = useLocation();

  return (
    <aside className="admin-sidebar" aria-label="Admin">
      <h2>Admin</h2>
      <nav>
        {links.map((link) => (
          <Link
            key={link.to}
            to={link.to}
            className={isActiveAdminLink(link, location.pathname) ? "active" : undefined}
            aria-current={isActiveAdminLink(link, location.pathname) ? "page" : undefined}
          >
            {link.label}
          </Link>
        ))}
      </nav>
    </aside>
  );
}

function isActiveAdminLink(link: AdminNavLink, pathname: string) {
  if (link.active) {
    return link.active(pathname);
  }
  return pathname === link.to || pathname.startsWith(`${link.to}/`);
}
