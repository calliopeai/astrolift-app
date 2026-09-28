import { STORAGE_KEY } from "@/lib/appearance";

/**
 * Stamps the appearance axes before first paint; the root layout renders it
 * inside <head>. Without this the page renders one theme and then swaps once
 * React hydrates, which reads as a flash. Mirrors what next-themes does for
 * light/dark.
 */
export function AppearanceBootScript() {
  return (
    <script
      // eslint-disable-next-line react/no-danger
      dangerouslySetInnerHTML={{
        __html: `(function(){try{var p=JSON.parse(localStorage.getItem(${JSON.stringify(
          STORAGE_KEY
        )})||"{}");var d=document.documentElement;
d.dataset.ground=["black","charcoal","emerald","paper","mist"].indexOf(p.ground)>-1?p.ground:"black";
d.dataset.accent=["green","copper","ice","periwinkle","amber"].indexOf(p.accent)>-1?p.accent:"green";
d.dataset.density=p.density==="cards"?"cards":"compact";
d.style.setProperty("--radius",([0,2,4,10].indexOf(p.corners)>-1?p.corners:2)+"px");}catch(e){}})();`,
      }}
    />
  );
}
