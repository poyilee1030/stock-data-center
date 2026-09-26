import { useState } from "react";

export function KeyDialog({ rejected, onSave, onCancel }: {
  rejected: boolean;
  onSave: (key: string) => void;
  onCancel?: () => void;
}) {
  const [key, setKey] = useState("");
  return (
    <div className="scrim" role="dialog" aria-modal="true" aria-labelledby="key-title">
      <form className="dialog" onSubmit={(e) => { e.preventDefault(); if (key.trim()) onSave(key.trim()); }}>
        <h2 id="key-title">輸入 API key</h2>
        <p className="muted">
          資料都經由公開 API 讀取，需要 <code>X-API-Key</code>。key 只存在這個瀏覽器（localStorage）。
          區網是明文 HTTP；經 Tailscale 連線則加密。
        </p>
        {rejected && <p className="error" role="alert">這個 key 不被接受，請重新輸入。</p>}
        <input type="password" autoFocus autoComplete="off" value={key} aria-label="API key"
               placeholder="STOCKDC_API_KEY" onChange={(e) => setKey(e.target.value)} />
        <div className="dialog-actions">
          {onCancel && <button type="button" className="ghost" onClick={onCancel}>取消</button>}
          <button type="submit" className="primary" disabled={!key.trim()}>儲存</button>
        </div>
      </form>
    </div>
  );
}
