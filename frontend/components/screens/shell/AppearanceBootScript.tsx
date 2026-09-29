import { GROUND_BACKGROUND, MIN_ACCENT_CONTRAST, STORAGE_KEY } from "@/lib/appearance";

/**
 * Stamps the appearance axes before first paint; the root layout renders it
 * inside <head>. Without this the page renders one theme and then swaps once
 * React hydrates, which reads as a flash. Mirrors what next-themes does for
 * light/dark.
 *
 * A custom accent (`#rrggbb`) gets the same treatment `applyAppearance` gives
 * it, including the contrast check against the ground, so what paints first
 * is what React keeps.
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
var a=p.accent,G=${JSON.stringify(GROUND_BACKGROUND)};
function L(h){var n=parseInt(h.slice(1),16);return [n>>16&255,n>>8&255,n&255].map(function(c){c/=255;return c<=.03928?c/12.92:Math.pow((c+.055)/1.055,2.4)}).reduce(function(s,c,i){return s+c*[.2126,.7152,.0722][i]},0)}
function C(x,y){var u=L(x),v=L(y);return (Math.max(u,v)+.05)/(Math.min(u,v)+.05)}
if(typeof a==="string"&&/^#[0-9a-f]{6}$/.test(a)&&C(a,G[d.dataset.ground])>=${MIN_ACCENT_CONTRAST}){d.dataset.accent="custom";d.style.setProperty("--brand-primary",a);d.style.setProperty("--brand-primary-dark",a);var k=C(a,"#000000")>=C(a,"#ffffff")?"#000000":"#ffffff";d.style.setProperty("--brand-on-primary",k);d.style.setProperty("--primary-foreground",k);}
else d.dataset.accent=["green","copper","ice","periwinkle","amber"].indexOf(a)>-1?a:"green";
d.dataset.density=p.density==="cards"?"cards":"compact";
d.style.setProperty("--radius",([0,2,4,10].indexOf(p.corners)>-1?p.corners:2)+"px");}catch(e){}})();`,
      }}
    />
  );
}
