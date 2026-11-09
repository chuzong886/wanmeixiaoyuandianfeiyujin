package campus.local;

import org.json.*;
import javax.crypto.*;
import javax.crypto.spec.*;
import javax.net.ssl.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.util.*;

public final class CampusClient {
    public interface Query {JSONObject query(JSONObject meter) throws Exception;}
    public static final class Failure extends IOException {public Failure(String message){super(message);}}
    public static String key(String stamp,String random) throws Exception {
        if(!stamp.matches("[0-9]{6,16}")||!random.matches("[A-Z0-9]{10}"))throw new Failure("校园加密参数无效");
        return ((Long.parseLong(stamp)%8)%2==1?stamp.substring(0,6):stamp.substring(stamp.length()-6))+random;
    }
    public static String crypt(String data,String stamp,String random,boolean encrypt) throws Exception {
        Cipher c=Cipher.getInstance("AES/ECB/PKCS5Padding");c.init(encrypt?Cipher.ENCRYPT_MODE:Cipher.DECRYPT_MODE,new SecretKeySpec(key(stamp,random).getBytes(StandardCharsets.UTF_8),"AES"));
        return encrypt?Base64.getEncoder().encodeToString(c.doFinal(data.getBytes(StandardCharsets.UTF_8))):new String(c.doFinal(Base64.getDecoder().decode(data)),StandardCharsets.UTF_8);
    }
    public static JSONObject query(JSONObject m) throws Exception {
        String stamp=String.valueOf(System.currentTimeMillis()),alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";StringBuilder r=new StringBuilder();SecureRandom rng=new SecureRandom();for(int i=0;i<10;i++)r.append(alphabet.charAt(rng.nextInt(alphabet.length())));String random=r.toString();
        JSONObject inner=new JSONObject().put("cmd","getbindroom").put("account",m.getString("account")).put("timestamp",stamp);
        JSONObject outer=new JSONObject().put("param",inner.toString()).put("customercode",m.getString("school_code")).put("method","getbindroom").put("command",m.optString("command","JBSWaterElecService"));
        String form="encryptData="+enc(crypt(outer.toString(),stamp,random,true))+"&timestamp="+stamp+"&randomStr="+random+"&method=getbindroom";
        JSONObject reply=post("https://xqh5.17wanxiao.com/smartWaterAndElectricityService/SWAEEncryptServlet",form,"application/x-www-form-urlencoded",true);
        if(reply.has("encryptData"))reply=new JSONObject(crypt(reply.getString("encryptData"),reply.getString("timestamp"),reply.getString("randomStr"),false));
        if(!reply.optString("result_").equalsIgnoreCase("true")||(reply.has("code_")&&!reply.optString("code_").equals("0")))throw new Failure("校园外层业务查询失败");
        Object raw=reply.opt("body");JSONObject body=raw instanceof JSONObject?(JSONObject)raw:new JSONObject(String.valueOf(raw));
        if(!body.optString("result").equals("0")||body.has("_result"))throw new Failure("校园内部业务查询失败");
        return select(body,m);
    }
    public static JSONObject select(JSONObject body,JSONObject m) throws Exception {
        if(body.optString("existflag").equals("0"))throw new Failure("校园账户未绑定宿舍");
        JSONArray rooms=body.optJSONArray("roomlist");if(rooms==null)rooms=new JSONArray().put(body);
        JSONArray available=new JSONArray(),matches=new JSONArray();
        for(int i=0;i<rooms.length();i++){
            JSONObject r=rooms.optJSONObject(i);if(r==null||r.optString("roomverify").isEmpty())continue;
            available.put(new JSONObject().put("roomverify",r.getString("roomverify")).put("label",r.optString("roomfullname",r.getString("roomverify"))));
            if(!m.optString("roomverify").isEmpty()&&!m.getString("roomverify").equals(r.getString("roomverify")))continue;
            JSONArray details=r.optJSONArray("detaillist");if(details==null)continue;
            for(int j=0;j<details.length();j++){
                JSONObject d=details.getJSONObject(j);if(!d.optString("businesstype").equals(m.optString("business_type","0")))continue;
                String raw=d.optString("odd");double balance;try{balance=Double.parseDouble(raw);}catch(Exception e){throw new Failure("剩余电量无效");}
                if(!Double.isFinite(balance))throw new Failure("剩余电量无效");
                matches.put(new JSONObject().put("balance",balance).put("balance_raw",raw).put("room",r.optString("roomfullname",r.getString("roomverify"))).put("roomverify",r.getString("roomverify")).put("series",series(m,r.getString("roomverify"))));
            }
        }
        if(matches.length()!=1)return new JSONObject().put("selection_required",true).put("rooms",available);
        return matches.getJSONObject(0).put("rooms",available);
    }
    public static String series(JSONObject m,String room) throws Exception {
        byte[] raw=MessageDigest.getInstance("SHA-256").digest((m.getString("school_code")+":"+m.getString("account")+":"+room+":"+m.optString("business_type","0")).getBytes(StandardCharsets.UTF_8));
        StringBuilder b=new StringBuilder();for(byte x:raw)b.append(String.format("%02x",x&255));return b.toString();
    }
    public static String enc(String s) throws Exception {return URLEncoder.encode(s,"UTF-8");}
    public static JSONObject post(String address,String payload,String type,boolean campus) throws Exception {
        URL url=new URL(address);if(!url.getProtocol().equals("https")||url.getUserInfo()!=null)throw new Failure("外部请求必须使用 HTTPS");
        HttpsURLConnection c=(HttpsURLConnection)url.openConnection();c.setInstanceFollowRedirects(false);c.setConnectTimeout(8000);c.setReadTimeout(25000);c.setRequestMethod("POST");c.setDoOutput(true);c.setRequestProperty("Content-Type",type+"; charset=utf-8");
        if(campus){c.setRequestProperty("Origin","https://xqh5.17wanxiao.com");c.setRequestProperty("Referer","https://xqh5.17wanxiao.com/userwaterelecmini/index.html");c.setRequestProperty("X-Requested-With","com.newcapec.mobile.ncp");}
        try{try(OutputStream o=c.getOutputStream()){o.write(payload.getBytes(StandardCharsets.UTF_8));}int code=c.getResponseCode();if(code<200||code>=300)throw new Failure("远端 HTTP "+code);
            try(InputStream in=c.getInputStream()){byte[] raw=readLimited(in,2*1024*1024);if(raw.length==0&&!campus)return new JSONObject();try{return new JSONObject(new String(raw,StandardCharsets.UTF_8));}catch(Exception e){if(!campus)return new JSONObject();throw new Failure("校园响应不是 JSON");}}
        }finally{c.disconnect();}
    }
    public static byte[] readLimited(InputStream in,int limit) throws Exception {ByteArrayOutputStream b=new ByteArrayOutputStream();byte[] buf=new byte[8192];int n;while((n=in.read(buf))!=-1){if(b.size()+n>limit)throw new Failure("响应过大");b.write(buf,0,n);}return b.toByteArray();}
}

