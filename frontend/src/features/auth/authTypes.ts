export type UserRole = "admin" | "viewer";

export type CurrentUser = {
  user_id: number;
  email: string;
  display_name: string;
  role: UserRole;
  status?: string;
};
