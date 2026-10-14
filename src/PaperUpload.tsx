import { useRef, useState } from 'react';
import { AlertCircle, FileText, Plus, Upload, Users, X } from 'lucide-react';
import './PaperUpload.css';

export function PaperUpload({onUpload}:{onUpload:(files:File[])=>void}) {
  const [files,setFiles]=useState<File[]>([]);
  const [error,setError]=useState('');
  const [dragging,setDragging]=useState(false);
  const input=useRef<HTMLInputElement>(null);
  const addFiles=(incoming:File[])=>{
    const valid=incoming.filter(file=>/\.pdf$/i.test(file.name)&&file.size>0);
    setError(valid.length<incoming.length?'내용이 있는 PDF 파일만 추가할 수 있습니다.':'');
    setFiles(previous=>[...previous,...valid.filter((file,index)=>!previous.some(item=>item.name===file.name&&item.size===file.size)&&!valid.slice(0,index).some(item=>item.name===file.name&&item.size===file.size))]);
  };
  return <form className="paper-upload" onSubmit={event=>{event.preventDefault();if(files.length)onUpload(files);}}>
    <div className="eyebrow">GROW OUR LIBRARY</div><h2 id="dialog-title">논문 추가</h2>
    <p className="muted">함께 읽고 싶은 논문을 팀 라이브러리에 추가하세요.</p>
    <div className={`upload-dropzone ${dragging?'is-dragging':''}`}
      onDragOver={event=>{event.preventDefault();setDragging(true);}}
      onDragLeave={event=>{if(!event.currentTarget.contains(event.relatedTarget as Node))setDragging(false);}}
      onDrop={event=>{event.preventDefault();setDragging(false);addFiles(Array.from(event.dataTransfer.files));}}>
      <span className="upload-symbol"><Upload size={24}/></span><strong>PDF를 여기에 끌어다 놓으세요</strong><span>여러 논문을 한 번에 추가할 수 있어요.</span>
      <button type="button" className="secondary" onClick={()=>input.current?.click()}><Plus size={15}/>PDF 선택</button>
      <input ref={input} className="sr-only" type="file" accept=".pdf,application/pdf" multiple aria-label="추가할 PDF 파일" onChange={event=>{addFiles(Array.from(event.target.files||[]));event.target.value='';}}/>
    </div>
    {error&&<p className="upload-error" role="alert"><AlertCircle size={14}/>{error}</p>}
    {!!files.length&&<div className="upload-file-list" aria-label="선택한 PDF">{files.map((file,index)=><div key={`${file.name}-${file.size}`}><FileText size={19}/><span><strong>{file.name}</strong><small>{file.size<1024*1024?`${Math.max(1,Math.round(file.size/1024))} KB`:`${(file.size/1024/1024).toFixed(1)} MB`}</small></span><button type="button" className="icon-button" aria-label={`${file.name} 선택 취소`} onClick={()=>setFiles(previous=>previous.filter((_,i)=>i!==index))}><X size={16}/></button></div>)}</div>}
    <p className="upload-sharing"><Users size={15}/>추가한 논문은 팀 전체가 함께 사용합니다.</p>
    <p className="fiction-note">목업에서는 파일명과 준비 상태만 표시합니다. 파일을 전송하거나 내용을 읽지 않으며 새로고침하면 초기화됩니다.</p>
    <button type="submit" className="primary full" disabled={!files.length}><Upload size={16}/>{files.length?`논문 ${files.length}편 추가`:'논문 추가'}</button>
  </form>;
}
