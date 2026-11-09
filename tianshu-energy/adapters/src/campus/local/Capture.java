package campus.local;
import org.json.*;
import java.util.*;
import java.util.regex.*;
import java.net.*;
/** Parses data only; never launches pasted curl or shell commands. */
public final class Capture {
    public static JSONObject parse(String text) throws Exception {
        if(text.length()>262144)throw new CampusClient.Failure("请求文本过长");
        List<JSONObject> candidates=new ArrayList<>();Set<String> seen=new HashSet<>();walk(text,candidates,seen,0);
        JSONArray out=new JSONArray();Set<String> keys=new HashSet<>();for(JSONObject c:candidates){String k=c.toString();if(keys.add(k))out.put(c);}if(out.length()==0)throw new CampusClient.Failure("未识别查询配置，请粘贴业务 JSON、HAR 或包含 encryptData/timestamp/randomStr 的请求");return new JSONObject().put("candidates",out);
    }
    static void walk(Object value,List<JSONObject> out,Set<String> seen,int depth) throws Exception {
        if(depth>12||value==null||value==JSONObject.NULL||seen.size()>2000)return;
        if(value instanceof JSONArray){JSONArray a=(JSONArray)value;for(int i=0;i<a.length();i++)walk(a.get(i),out,seen,depth+1);return;}
        if(value instanceof JSONObject){JSONObject j=(JSONObject)value;
            if(j.has("encryptData")&&j.has("timestamp")&&j.has("randomStr")){try{walk(CampusClient.crypt(j.getString("encryptData"),j.optString("timestamp"),j.optString("randomStr"),false),out,seen,depth+1);}catch(Exception ignored){}}
            String school=j.optString("school_code",j.optString("customercode")),account=j.optString("account");
            Object param=j.opt("param");if(param instanceof String){try{param=new JSONObject((String)param);}catch(Exception ignored){}}if(param instanceof JSONObject&&account.isEmpty())account=((JSONObject)param).optString("account");
            if(!school.isEmpty()&&!account.isEmpty())out.add(new JSONObject().put("school_code",school).put("account",account).put("roomverify",j.optString("roomverify",param instanceof JSONObject?((JSONObject)param).optString("roomverify"):"")).put("command",j.optString("command","JBSWaterElecService")));
            // HAR postData.params is a list of name/value pairs.
            JSONArray params=j.optJSONArray("params");if(params!=null){JSONObject fields=new JSONObject();for(int i=0;i<params.length();i++){JSONObject p=params.optJSONObject(i);if(p!=null&&p.has("name"))fields.put(p.getString("name"),p.optString("value"));}walk(fields,out,seen,depth+1);}
            Iterator<String> it=j.keys();while(it.hasNext())walk(j.get(it.next()),out,seen,depth+1);return;
        }
        if(!(value instanceof String))return;String s=((String)value).trim();if(s.length()<2||!seen.add(s))return;
        try{if(s.startsWith("{")){walk(new JSONObject(s),out,seen,depth+1);return;}if(s.startsWith("[")){walk(new JSONArray(s),out,seen,depth+1);return;}}catch(Exception ignored){}
        if(s.contains("%7B")||s.contains("%7b"))try{walk(URLDecoder.decode(s,"UTF-8"),out,seen,depth+1);}catch(Exception ignored){}
        JSONObject fields=new JSONObject();Matcher m=Pattern.compile("(?:^|[&?\\s'\"])(encryptData|timestamp|randomStr|customercode|school_code|account|roomverify|command)=([^&\\s'\"]+)").matcher(s);while(m.find()){String raw=m.group(2);fields.put(m.group(1),URLDecoder.decode(raw,"UTF-8"));}if(fields.length()>0)walk(fields,out,seen,depth+1);
        // curl --data and HTTP JSON body, without interpreting any shell syntax.
        for(int start=s.indexOf('{');start>=0;start=s.indexOf('{',start+1)){int end=s.lastIndexOf('}');if(end>start)try{walk(new JSONObject(s.substring(start,end+1)),out,seen,depth+1);break;}catch(Exception ignored){}}
    }
}
