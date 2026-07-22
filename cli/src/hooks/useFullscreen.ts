import { useEffect } from 'react';

/** Mouse reporting временно отключён — вызывает флуд escape-последовательностей
 *  в Ink и блокирует выделение текста. Кликабельные tool-блоки вернём
 *  когда починим конфликт useMouse/useInput за stdin. */
export function useFullscreen(_enabled = true) {
  useEffect(() => {
    return () => {};
  }, []);
}