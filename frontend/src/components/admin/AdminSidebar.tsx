import { NavLink } from "react-router-dom";

const links = [
  { to: "/admin/documents", label: "Documents" },
  { to: "/admin/documents/review", label: "Review" },
  { to: "/admin/evaluations", label: "Evaluations" },
  { to: "/admin/retrieval-debug", label: "Retrieval Debug" },
  { to: "/admin/jobs", label: "Jobs" }
];

export function AdminSidebar() {
  return (
    <aside className="admin-sidebar" aria-label="Admin">
      <h2>Admin</h2>
      <nav>
        {links.map((link) => (
          <NavLink key={link.to} to={link.to} className={({ isActive }) => (isActive ? "active" : undefined)}>
            {link.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
