package campus.local;
import org.json.*;
import java.io.*;
import java.security.SecureRandom;
/** Pure protocol/forecast adapter: no network, scheduler, database or second application. */
public final class ApiAdapter {
 public static void main(String[] args)throws Exception{
  JSONObject out;
  try{
   JSONObject b=new JSONObject(new String(CampusClient.readLimited(System.in,2*1024*1024),"UTF-8"));String action=b.getString("action");
   if(action.equals("prepare")){
    JSONObject m=b.getJSONObject("config");String stamp=String.valueOf(System.currentTimeMillis()),alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";StringBuilder r=new StringBuilder();SecureRandom rng=new SecureRandom();for(int i=0;i<10;i++)r.append(alphabet.charAt(rng.nextInt(alphabet.length())));
    JSONObject inner=new JSONObject().put("cmd","getbindroom").put("account",m.getString("account")).put("timestamp",stamp);
    JSONObject outer=new JSONObject().put("param",inner.toString()).put("customercode",m.getString("school_code")).put("method","getbindroom").put("command",m.optString("command","JBSWaterElecService"));
    out=new JSONObject().put("encryptData",CampusClient.crypt(outer.toString(),stamp,r.toString(),true)).put("timestamp",stamp).put("randomStr",r.toString()).put("method","getbindroom");
   }else if(action.equals("decode")){
    JSONObject reply=b.getJSONObject("response"),m=b.getJSONObject("config");
    if(reply.has("encryptData"))reply=new JSONObject(CampusClient.crypt(reply.getString("encryptData"),reply.getString("timestamp"),reply.getString("randomStr"),false));
    if(!reply.optString("result_").equalsIgnoreCase("true")||(reply.has("code_")&&!reply.optString("code_").equals("0")))throw new CampusClient.Failure("校园外层业务查询失败");
    Object raw=reply.opt("body");JSONObject body=raw instanceof JSONObject?(JSONObject)raw:new JSONObject(String.valueOf(raw));
    if(!body.optString("result").equals("0")||body.has("_result"))throw new CampusClient.Failure("校园内部业务查询失败");
    out=CampusClient.select(body,m);
   }else if(action.equals("forecast")){
    JSONArray history=b.getJSONArray("history");JSONObject o=Forecast.options(b);double now=b.optDouble("now",Forecast.now());JSONObject a=Forecast.analyse(history,o,now);JSONArray intervals=Forecast.intervals(history);
    out=new JSONObject().put("analysis",a).put("forecast",Forecast.forecast(a,history.length()>0?history.getJSONObject(history.length()-1):null,o,now)).put("intervals",intervals).put("daily",Forecast.daily(intervals,30,now));
   }else if(action.equals("capture")){out=Capture.parse(b.getString("source"));}
   else throw new CampusClient.Failure("适配操作无效");
  }catch(Exception e){out=new JSONObject().put("error",e instanceof CampusClient.Failure?e.getMessage():"接口参数或返回格式无效，请核对配置");}
  System.out.write(out.toString().getBytes("UTF-8"));System.out.flush();
 }
}
