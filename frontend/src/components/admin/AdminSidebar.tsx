import { Link, useLocation } from "react-router-dom";
import { AdminIcon, type AdminIconName } from "./AdminIcon";

type AdminNavLink = {
  active?: (pathname: string) => boolean;
  icon: AdminIconName;
  label: string;
  to: string;
};

type AdminNavGroup = {
  label: string;
  links: AdminNavLink[];
};

const dashboardLink: AdminNavLink = {
  to: "/admin",
  label: "ダッシュボード",
  icon: "dashboard",
  active: (pathname: string) => pathname === "/admin"
};

const navGroups: AdminNavGroup[] = [
  {
    label: "ナレッジ管理",
    links: [
      {
        to: "/admin/documents",
        label: "ドキュメント",
        icon: "documents",
        active: (pathname: string) =>
          pathname === "/admin/documents" ||
          (pathname.startsWith("/admin/documents/") && !pathname.startsWith("/admin/documents/review"))
      },
      {
        to: "/admin/documents/review",
        label: "承認",
        icon: "review",
        active: (pathname: string) => pathname === "/admin/documents/review"
      }
    ]
  },
  {
    label: "品質・検証",
    links: [
      { to: "/admin/evaluations", label: "評価", icon: "evaluations" },
      { to: "/admin/retrieval-debug", label: "検索デバッグ", icon: "debug" }
    ]
  },
  {
    label: "システム",
    links: [{ to: "/admin/jobs", label: "ジョブ", icon: "jobs" }]
  }
];

export function AdminSidebar() {
  const location = useLocation();

  return (
    <aside className="admin-sidebar" aria-label="管理メニュー">
      <Link className="admin-sidebar-home" to="/admin">
        <span className="admin-brand-mark">
          <AdminIcon name="spark" />
        </span>
        <span>
          <strong>RAG Admin</strong>
          <small>運用コンソール</small>
        </span>
      </Link>
      <nav>
        <AdminNavItem link={dashboardLink} pathname={location.pathname} />
        {navGroups.map((group) => (
          <section className="admin-nav-group" key={group.label}>
            <h2>{group.label}</h2>
            {group.links.map((link) => (
              <AdminNavItem key={link.to} link={link} pathname={location.pathname} />
            ))}
          </section>
        ))}
      </nav>
    </aside>
  );
}

function AdminNavItem({ link, pathname }: { link: AdminNavLink; pathname: string }) {
  const active = isActiveAdminLink(link, pathname);
  return (
    <Link
      to={link.to}
      className={active ? "active" : undefined}
      aria-current={active ? "page" : undefined}
    >
      <AdminIcon name={link.icon} />
      <span>{link.label}</span>
    </Link>
  );
}

function isActiveAdminLink(link: AdminNavLink, pathname: string) {
  if (link.active) {
    return link.active(pathname);
  }
  return pathname === link.to || pathname.startsWith(`${link.to}/`);
}
