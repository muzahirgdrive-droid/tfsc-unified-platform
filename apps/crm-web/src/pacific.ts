/** Resolve wall-clock input using IANA timezone data, independent of the device timezone. */
export function pacificCandidates(local:string):{iso:string,label:string}[]{
  if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(local))return [];
  const stamp=Date.parse(local+':00Z');if(!Number.isFinite(stamp))return [];
  const formatter=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Los_Angeles',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  const result:{iso:string,label:string}[]=[];
  // Pacific civil offsets in the supported scheduling window. Verify every candidate against IANA data.
  for(const offset of [-7,-8]){
    const instant=new Date(stamp-offset*3_600_000),p=Object.fromEntries(formatter.formatToParts(instant).map(x=>[x.type,x.value]));
    if(`${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`===local){result.push({iso:instant.toISOString(),label:offset===-7?'First occurrence · Pacific daylight time (UTC−07:00)':'Pacific standard time (UTC−08:00)'});}
  }
  return result;
}
