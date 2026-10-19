// 只缓存不可变的原图；标注仍实时读取，避免覆盖其他任务的修改。
const MAX_IMAGES = 6;
const images = new Map<
  string,
  { image: HTMLImageElement; ready: Promise<HTMLImageElement> }
>();

export function loadImage(url: string): Promise<HTMLImageElement> {
  const cached = images.get(url);
  if (cached) {
    images.delete(url);
    images.set(url, cached);
    return cached.ready;
  }
  const image = new window.Image();
  image.decoding = "async";
  const ready = new Promise<HTMLImageElement>((resolve, reject) => {
    image.onload = () => resolve(image);
    image.onerror = () => {
      if (images.get(url)?.image === image) images.delete(url);
      reject(new Error("图片加载失败"));
    };
  });
  images.set(url, { image, ready });
  while (images.size > MAX_IMAGES) images.delete(images.keys().next().value!);
  image.src = url;
  return ready;
}

export function cachedImage(url: string): HTMLImageElement | undefined {
  const image = images.get(url)?.image;
  return image?.complete && image.naturalWidth > 0 ? image : undefined;
}
