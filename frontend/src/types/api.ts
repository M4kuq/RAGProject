export type ApiResponse<T> = {
  data: T;
  meta?: {
    request_id?: string;
    pagination?: PaginationMeta;
  };
};

export type PaginationMeta = {
  page: number;
  page_size: number;
  total: number;
  has_next: boolean;
};

