'use client';

import { useEffect, useState } from 'react';

function readMode() {
  try { return window.localStorage.getItem('v2_advanced_mode') === '1'; } catch { return false; }
}

export function useAdvancedMode() {
  const [advanced, setAdvanced] = useState(false);
  useEffect(() => {
    const update = () => setAdvanced(readMode());
    update();
    window.addEventListener('v2-mode-change', update);
    window.addEventListener('storage', update);
    return () => {
      window.removeEventListener('v2-mode-change', update);
      window.removeEventListener('storage', update);
    };
  }, []);
  return advanced;
}
