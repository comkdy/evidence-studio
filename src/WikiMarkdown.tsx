import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Local evidence and registered Wiki paths only. Never execute embedded HTML or images.
export function WikiMarkdown({text,path='answer.md'}:{text:string;path?:string}){
 function link(url:string){
  if(/^\/api\/pilot\/(claims\/|documents\/|wiki-files\/|answer-runs\/[^/]+\/claims\/)/.test(url))return url;
  if(url.startsWith('#'))return url;
  if(/^[a-z][a-z0-9+.-]*:/i.test(url)||url.startsWith('/'))return '';
  const resolved=new URL(url,'https://wiki.invalid/'+path);
  if(resolved.origin!=='https://wiki.invalid')return '';
  return '/api/pilot/wiki-file?path='+encodeURIComponent(decodeURIComponent(resolved.pathname.slice(1)));
 }
 return <div className="pilot-markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={['img']} urlTransform={link} components={{a:({children,href})=><a href={href} target="_blank" rel="noreferrer">{children}</a>}}>{text}</Markdown></div>;
}
