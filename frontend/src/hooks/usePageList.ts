import { useCallback, useEffect, useRef, useState } from "react";
import type { PaginationProps } from "antd";
import api from "../api/http";

export interface PageListOptions<Row, Filters extends object> {
  /** 路由前缀已含的 API 路径，如 /incidents */
  path: string;
  filters?: Filters;
  /** 服务端分页默认 100，控制台默认 20 */
  pageSize?: number;
  /** 响应可能是分页对象也可能是裸数组 */
  array?: (data: unknown) => Row[];
  deps?: unknown[];
}

interface PageShape<Row> {
  items: Row[];
  page: number;
  page_size: number;
  total: number;
}

export const asPage = <Row,>(data: unknown): PageShape<Row> => {
  if (Array.isArray(data)) {
    return { items: data as Row[], page: 1, page_size: data.length, total: data.length };
  }
  const shape = data as PageShape<Row> | null | undefined;
  return {
    items: shape?.items ?? [],
    page: shape?.page ?? 1,
    page_size: shape?.page_size ?? 0,
    total: shape?.total ?? 0,
  };
};

export function usePageList<Row, Filters extends object = object>(
  path: string,
  filters?: Filters,
  pageSize = 20,
) {
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const filterKey = JSON.stringify(filters ?? {});
  const requestId = useRef(0);

  const load = useCallback(
    async (targetPage: number) => {
      const current = ++requestId.current;
      setLoading(true);
      setError(null);
      try {
        const params: Record<string, unknown> = { page: targetPage, page_size: pageSize, ...(filters ?? {}) };
        const data = (await api.get(path, { params })).data;
        if (current !== requestId.current) return;
        const shape = asPage<Row>(data);
        setRows(shape.items);
        setTotal(shape.total);
        setPage(targetPage);
      } catch (requestError) {
        if (current !== requestId.current) return;
        const { errorMessage } = await import("../api/http");
        setError(errorMessage(requestError, "数据加载失败"));
      } finally {
        if (current === requestId.current) setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [path, filterKey, pageSize],
  );

  useEffect(() => { void load(1); }, [load]);

  const refresh = useCallback(() => { void load(page); }, [load, page]);

  const pagination: Partial<PaginationProps> = {
    current: page,
    pageSize,
    total,
    showSizeChanger: false,
    showTotal: (total: number) => `共 ${total} 条`,
    onChange: (nextPage: number) => void load(nextPage),
  };

  return { rows, loading, error, pagination, refresh, reload: load, page };
}

export function useDetail<Detail>(path: string | null) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!path) { setDetail(null); setError(null); return; }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.get<Detail>(path)
      .then((response) => { if (!cancelled) setDetail(response.data); })
      .catch((requestError) => {
        if (cancelled) return;
        import("../api/http").then(({ errorMessage }) =>
          setError(errorMessage(requestError, "详情加载失败")));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [path]);

  return { detail, loading, error };
}
