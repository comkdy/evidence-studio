// Display-only normalization. Raw database values and verbatim source quotes stay unchanged.
const superscripts:Record<string,string>={'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹','-':'⁻','+':'⁺'};
const plainSuperscripts:Record<string,string>=Object.fromEntries(Object.entries(superscripts).map(([a,b])=>[b,a]));
const up=(text:string)=>[...text].map(c=>superscripts[c]??c).join('');
const number='[+−-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)';
const exponent='(?:\\^\\s*(?:\\{([+−-]?\\d{1,4})\\}|([+−-]?\\d{1,4}))|([⁺⁻⁰¹²³⁴⁵⁶⁷⁸⁹]+))';
const scientific=new RegExp('(?<![\\p{L}\\p{N}_.])(?:('+number+')\\s*[×x*·]\\s*)?10'+exponent+'(?![\\p{N}^])|(?<![\\p{L}\\p{N}_.])('+number+')[eE]([+−-]?\\d{1,4})(?![\\p{L}\\p{N}_.])','gu');

function normalized(mantissa:string, power:string){
 const negative=/^[−-]/.test(mantissa),positive=mantissa.startsWith('+');
 const unsigned=mantissa.replace(/^[+−-]/,'');
 const [integer,fraction='']=unsigned.split('.');
 const digits=integer+fraction,first=digits.search(/[1-9]/);
 if(first<0)return mantissa.replace('-','−');
 let significant=digits.slice(first);
 // Decimal trailing zeros are reported precision; keep them.
 if(!unsigned.includes('.'))significant=significant.replace(/0+$/,'');
 const coefficient=(negative?'−':positive?'+':'')+significant[0]+(significant.length>1?'.'+significant.slice(1):'');
 const exp=Number(power.replace('−','-'))+integer.length-first-1;
 if(!Number.isFinite(exp)||Math.abs(exp)>9999)return mantissa+' × 10^'+power;
 return (coefficient==='1'?'':coefficient+' × ')+'10'+up(String(exp));
}

export function scientificNotation(text:string):string{
 // Stash formatted expressions so the plain-number pass cannot alter exponents.
 const expressions:string[]=[];
 let value=text.replace(/(?<![\p{L}\p{N}_.])(\d+(?:\.\d+)?)k(?=\s|$|[,;)])/gu,(_match,coefficient:string)=>normalized(coefficient,'3')).replace(scientific,(_match,coefficient:string|undefined,braced:string|undefined,caret:string|undefined,unicode:string|undefined,eCoefficient:string|undefined,ePower:string|undefined)=>{
  const power=ePower??braced??caret??[...(unicode??'')].map(c=>plainSuperscripts[c]).join('');
  expressions.push(normalized(eCoefficient??coefficient??'1',power));
  return '\uE000'+String.fromCodePoint(0xE100+expressions.length-1)+'\uE001';
 });
 value=value.replace(/(?<![\p{L}\p{N}_.^])[+−-]?(?:\d+(?:\.\d+)?|\.\d+)(?![\p{L}\p{N}_.⁰¹²³⁴⁵⁶⁷⁸⁹])/gu,(raw,offset:number,whole:string)=>{
  // Avoid identifier/version fragments, grouped thousands and multiplication bases.
  if(whole[offset-1]===','||whole[offset+raw.length]===','||/^0\d/.test(raw))return raw;
  const absolute=Math.abs(Number(raw.replace('−','-')));
  return absolute!==0&&(absolute>=10000||absolute<0.001)?normalized(raw,'0'):raw.replace(/^-/,'−');
 });
 value=value.replace(/\uE000([\uE100-\uF8FF])\uE001/g,(_match,token:string)=>expressions[token.codePointAt(0)!-0xE100]);
 // Explicit unit powers; never infer a lost exponent such as "cm2" or "10 2".
 value=value.replace(/([A-Za-zμµΩ]+)\^\s*(?:\{([+−-]?\d{1,3})\}|([+−-]?\d{1,3}))/g,(_match,unit:string,a:string,b:string)=>unit+up((a??b).replace('−','-')));
 return value.replace(/µ/g,'μ').replace(/(\d(?:\.\d+)?)x(?=\s|$|[,;)])/g,'$1×').replace(/<=\s*(?=[+−-]?\d)/g,'≤').replace(/>=\s*(?=[+−-]?\d)/g,'≥');
}
