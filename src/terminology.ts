import glossary from '../shared/terminology.json' with { type: 'json' };

// Search-only normalization. Never apply this to quotations, user text, or saved answers.
const aliases=glossary.terms.flatMap(term=>term.aliases.map(alias=>({alias:alias.toLowerCase(),term:term.preferredEn.toLowerCase()}))).sort((a,b)=>b.alias.length-a.alias.length);
const escaped=(text:string)=>text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
const alternatives=aliases.map(({alias})=>/[a-z0-9]/i.test(alias)?`(?<![a-z0-9])${escaped(alias)}(?![a-z0-9])`:escaped(alias));
const pattern=new RegExp(alternatives.join('|'),'gu');
const canonical=new Map(aliases.map(item=>[item.alias,item.term]));

export function normalizeSearchText(text:string):string {
  return text.normalize('NFKC').toLowerCase().replace(pattern,match=>canonical.get(match)??match).replace(/\s+/g,' ').trim();
}
