import { useEffect, useMemo, useState } from "react";
import { getPlans } from "./api";

/* What this visitor can reach, in one object.
 *
 * Entitlements and limits are decided by the server (billing.py) and mirrored
 * here for presentation only -- every gate is enforced again on the request.
 * The free ceilings come from /billing/plans rather than /auth/me because the
 * studio has to show them to someone who has not signed in yet.
 */

const FALLBACK = { max_clips: 2, max_source_minutes: 30, max_upload_mb: 500 };

export function usePlan(user) {
  const [catalogue, setCatalogue] = useState(null);

  useEffect(() => { getPlans().then(setCatalogue).catch(() => {}); }, []);

  return useMemo(() => {
    const entitlements = user?.entitlements || [];
    const limits = user?.limits || catalogue?.limits?.free || FALLBACK;
    const can = (feature) => entitlements.includes(feature);
    return {
      plan: user?.plan || "free",
      /* Derived from what the account can actually DO, not from the plan name.
         The admin bypass (ADMIN_EMAILS) grants every entitlement while leaving
         the plan column at "free", so a plan-string check told admins their
         exports would be watermarked when the server had already decided they
         would not be. Any future comped account has the same shape. */
      isFree: entitlements.length === 0,
      watermarked: !can("no_watermark"),
      isAdmin: !!user?.is_admin,
      limits,
      credits: user?.credits ?? null,
      creditsPerClip: catalogue?.credits_per_clip ?? 10,
      can,
    };
  }, [user, catalogue]);
}
