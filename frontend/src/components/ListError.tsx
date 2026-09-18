import { Alert, Button } from "antd";

/**
 * Persistent inline error for a data region, with a retry affordance.
 *
 * Exists because `usePageList` used to hand back an `error` that no page
 * rendered, so a 500 or an unreachable backend read as an empty dataset. A list
 * page must be able to tell "there is no data" apart from "we could not ask".
 *
 * Renders `null` when there is no error, so it is safe to place unconditionally.
 */
export function ListError({
  error,
  onRetry,
  description,
}: {
  error?: string | null;
  onRetry?: () => void;
  description?: string;
}) {
  if (!error) return null;
  return (
    <Alert
      type="error"
      showIcon
      message="数据加载失败"
      description={description ? `${error} · ${description}` : error}
      action={
        onRetry ? (
          // antd's Button inserts a typographic space between two CJK
          // characters, so the rendered label is "重 试" and that stray space
          // would otherwise leak into the control's accessible name. Pinning
          // aria-label keeps the retry action announced — and reachable — as
          // one word.
          <Button size="small" aria-label="重试" onClick={onRetry}>
            重试
          </Button>
        ) : undefined
      }
      style={{ marginBottom: 12 }}
    />
  );
}

export default ListError;
